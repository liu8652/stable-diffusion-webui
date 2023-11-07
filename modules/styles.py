import csv
import fnmatch
import os
import os.path
import re
import typing
import shutil


class PromptStyle(typing.NamedTuple):
    name: str
    prompt: str
    negative_prompt: str
    path: str = None


def merge_prompts(style_prompt: str, prompt: str) -> str:
    if "{prompt}" in style_prompt:
        res = style_prompt.replace("{prompt}", prompt)
    else:
        parts = filter(None, (prompt.strip(), style_prompt.strip()))
        res = ", ".join(parts)

    return res


def apply_styles_to_prompt(prompt, styles):
    for style in styles:
        prompt = merge_prompts(style, prompt)

    return prompt


# A dictionary of regular expressions to tidy up the prompt text
re_list = [
    {"a_before_vowel", re.compile("([Aa])\s+([AEIOUaeiou])"), "$1n $2"},
    {"multiple commas", re.compile("(,+\s+)+,?"), ", "},
    {"multiple spaces", re.compile("\s{2,}"), " "},
]


def extract_style_text_from_prompt(style_text, prompt):
    for var in ["style_text", "prompt"]:
        text = locals()[var]
        for _, regex, replace in re_list:
            text = regex.sub(" ", replace, text)
        locals()[var] = text

    if "{prompt}" in style_text:
        left, right = style_text.split("{prompt}", 2)
        if prompt.startswith(left) and prompt.endswith(right):
            prompt = prompt[len(left) : len(prompt) - len(right)]
            return True, prompt
    else:
        if prompt.endswith(style_text):
            prompt = prompt[: len(prompt) - len(style_text)]

            if prompt.endswith(", "):
                prompt = prompt[:-2]

            return True, prompt

    return False, prompt


def extract_style_from_prompts(style: PromptStyle, prompt, negative_prompt):
    if not style.prompt and not style.negative_prompt:
        return False, prompt, negative_prompt

    match_positive, extracted_positive = extract_style_text_from_prompt(
        style.prompt, prompt
    )
    if not match_positive:
        return False, prompt, negative_prompt

    match_negative, extracted_negative = extract_style_text_from_prompt(
        style.negative_prompt, negative_prompt
    )
    if not match_negative:
        return False, prompt, negative_prompt

    return True, extracted_positive, extracted_negative


class StyleDatabase:
    def __init__(self, path: str):
        self.no_style = PromptStyle("None", "", "", None)
        self.styles = {}
        self.path = path

        folder, file = os.path.split(self.path)
        self.default_file = file.split("*")[0] + ".csv"
        if self.default_file == ".csv":
            self.default_file = "styles.csv"
        self.default_path = os.path.join(folder, self.default_file)

        self.prompt_fields = [field for field in PromptStyle._fields if field != "path"]

        self.reload()

    def reload(self):
        self.styles.clear()

        path, filename = os.path.split(self.path)

        # if the filename component of the path contains a wildcard,
        # e.g. styles*.csv, load all matching files
        if "*" in filename:
            fileglob = filename.split("*")[0] + "*.csv"
            for file in os.listdir(path):
                if fnmatch.fnmatch(file, fileglob):
                    self.styles[file.upper()] = PromptStyle(
                        f"{file.upper()}", None, None, "divider"
                    )
                    self.load_from_csv(os.path.join(path, file))
        elif not os.path.exists(self.path):
            print(f"Style database not found: {self.path}")
            return
        else:
            self.load_from_csv(self.path)

    def load_from_csv(self, path: str):
        with open(path, "r", encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file, skipinitialspace=True)
            for row in reader:
                # Ignore empty rows or rows starting with a comment
                if not row or row["name"].startswith("#"):
                    continue
                # Support loading old CSV format with "name, text"-columns
                prompt = row["prompt"] if "prompt" in row else row["text"]
                negative_prompt = row.get("negative_prompt", "")
                # Add style to database
                self.styles[row["name"]] = PromptStyle(
                    row["name"], prompt, negative_prompt, path
                )

    def get_style_prompts(self, styles):
        return [self.styles.get(x, self.no_style).prompt for x in styles]

    def get_negative_style_prompts(self, styles):
        return [self.styles.get(x, self.no_style).negative_prompt for x in styles]

    def apply_styles_to_prompt(self, prompt, styles):
        return apply_styles_to_prompt(
            prompt, [self.styles.get(x, self.no_style).prompt for x in styles]
        )

    def apply_negative_styles_to_prompt(self, prompt, styles):
        return apply_styles_to_prompt(
            prompt, [self.styles.get(x, self.no_style).negative_prompt for x in styles]
        )

    def save_styles(self, path: str = None) -> None:
        # The path argument is deprecated, but kept for backwards compatibility
        _ = path

        # Update any styles without a path to the default path
        for style in list(self.styles.values()):
            if not style.path:
                self.styles[style.name] = style._replace(path=self.default_path)

        # Create a list of all distinct paths, including the default path
        style_paths = set()
        style_paths.add(self.default_path)
        for _, style in self.styles.items():
            if style.path:
                style_paths.add(style.path)

        csv_names = [os.path.split(path)[1].lower() for path in style_paths]

        for style_path in style_paths:
            # Always keep a backup file around
            if os.path.exists(style_path):
                shutil.copy(style_path, f"{style_path}.bak")

            # Write the styles to the CSV file
            with open(style_path, "w", encoding="utf-8-sig", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=self.prompt_fields)
                writer.writeheader()
                for style in (s for s in self.styles.values() if s.path == style_path):
                    # Skip style list dividers, e.g. "STYLES.CSV"
                    if style.path.lower() == "divider":
                        continue
                    if style.name.lower().strip("# ") in csv_names:
                        continue
                    # Write style fields, ignoring the path field
                    writer.writerow(
                        {k: v for k, v in style._asdict().items() if k != "path"}
                    )

    def extract_styles_from_prompt(self, prompt, negative_prompt):
        extracted = []

        applicable_styles = list(self.styles.values())

        while True:
            found_style = None

            for style in applicable_styles:
                is_match, new_prompt, new_neg_prompt = extract_style_from_prompts(
                    style, prompt, negative_prompt
                )
                if is_match:
                    found_style = style
                    prompt = new_prompt
                    negative_prompt = new_neg_prompt
                    break

            if not found_style:
                break

            applicable_styles.remove(found_style)
            extracted.append(found_style.name)

        return list(reversed(extracted)), prompt, negative_prompt
