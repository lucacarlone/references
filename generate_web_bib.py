from pathlib import Path
import re

INPUT_FILE = Path("myRefs.bib")
OUTPUT_FILE = Path("myRefs-web.bib")


STRING_LINE_RE = re.compile(
    r"""
    ^\s*
    @string
    \s*\{
    \s*(?P<name>[A-Za-z_][A-Za-z0-9_:\-]*)
    \s*=\s*
    (?P<value>.+)
    \}
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

COMMENT_LINE_RE = re.compile(
    r"^\s*@comment\s*\{.*\}\s*$",
    re.IGNORECASE,
)

ENTRY_START_RE = re.compile(
    r"""
    ^\s*
    @
    (?!string\b|comment\b|preamble\b)
    [A-Za-z]+
    \s*[\{\(]
    """,
    re.IGNORECASE | re.MULTILINE | re.VERBOSE,
)

YEAR_RE = re.compile(
    r"""
    ^\s*
    year
    \s*=\s*
    [\{"']?
    (?P<year>\d{4})
    [\}"']?
    \s*,?
    \s*$
    """,
    re.IGNORECASE | re.MULTILINE | re.VERBOSE,
)

AUTHOR_RE = re.compile(
    r"^\s*author\s*=",
    re.IGNORECASE | re.MULTILINE,
)

EDITOR_RE = re.compile(
    r"""
    ^(?P<indent>\s*)
    editor
    \s*=\s*
    (?P<value>
        \{
            (?:
                [^{}]
                |
                \{[^{}]*\}
            )*
        \}
        |
        "[^"]*"
    )
    \s*,?
    \s*$
    """,
    re.IGNORECASE | re.MULTILINE | re.VERBOSE,
)


def unwrap_value(value: str) -> str:
    """
    Remove the outer braces or quotes from an @string value.

    Example:
        {{IEEE} Trans. Robotics}
    becomes:
        {IEEE} Trans. Robotics
    """
    value = value.strip().rstrip(",").strip()

    if value.startswith("{") and value.endswith("}"):
        return value[1:-1]

    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]

    return value


def extract_strings_and_clean(text: str) -> tuple[dict[str, str], str]:
    """
    Extract one-line @string declarations while preserving all
    publication entries.
    """
    strings: dict[str, str] = {}
    retained_lines: list[str] = []

    for line in text.splitlines():
        string_match = STRING_LINE_RE.match(line)

        if string_match:
            name = string_match.group("name").lower()
            value = unwrap_value(string_match.group("value"))
            strings[name] = value
            continue

        if COMMENT_LINE_RE.match(line):
            continue

        retained_lines.append(line)

    return strings, "\n".join(retained_lines) + "\n"


def expand_string_fields(text: str, strings: dict[str, str]) -> str:
    """
    Expand bare @string identifiers in venue-related fields.

    Example:
        booktitle = rss,

    becomes:
        booktitle = {Robotics: Science and Systems (RSS)},
    """
    fields = (
        "journal",
        "booktitle",
        "publisher",
        "institution",
        "school",
        "organization",
        "series",
        "address",
    )

    field_names = "|".join(re.escape(field) for field in fields)

    pattern = re.compile(
        rf"""
        (?P<prefix>
            \b(?:{field_names})
            \s*=\s*
        )
        (?P<identifier>
            [A-Za-z_][A-Za-z0-9_:\-]*
        )
        (?P<suffix>
            \s*(?:,|\}})
        )
        """,
        re.IGNORECASE | re.VERBOSE,
    )

    def replacement(match: re.Match[str]) -> str:
        identifier = match.group("identifier")
        expanded = strings.get(identifier.lower())

        if expanded is None:
            return match.group(0)

        return (
            f"{match.group('prefix')}"
            f"{{{expanded}}}"
            f"{match.group('suffix')}"
        )

    return pattern.sub(replacement, text)


def find_entry_end(text: str, start: int) -> int:
    """
    Return the position immediately after a complete BibTeX entry.

    Nested braces and quoted strings are handled, so braces inside titles
    and other field values do not terminate the entry prematurely.
    """
    opening_match = re.match(
        r"\s*@[A-Za-z]+\s*([\{\(])",
        text[start:],
        re.IGNORECASE,
    )

    if opening_match is None:
        raise RuntimeError(
            f"Could not parse the BibTeX entry near character {start}."
        )

    opening = opening_match.group(1)
    closing = "}" if opening == "{" else ")"
    opening_position = start + opening_match.end() - 1

    depth = 0
    in_quotes = False
    escaped = False

    for index in range(opening_position, len(text)):
        character = text[index]

        if escaped:
            escaped = False
            continue

        if character == "\\":
            escaped = True
            continue

        if character == '"':
            in_quotes = not in_quotes
            continue

        if in_quotes:
            continue

        if character == opening:
            depth += 1
        elif character == closing:
            depth -= 1

            if depth == 0:
                return index + 1

    raise RuntimeError(
        f"Unclosed BibTeX entry beginning near character {start}."
    )


def split_publication_entries(text: str) -> list[str]:
    """
    Extract all publication entries as complete BibTeX blocks.
    """
    entries: list[str] = []
    position = 0

    while True:
        match = ENTRY_START_RE.search(text, position)

        if match is None:
            break

        start = match.start()
        end = find_entry_end(text, start)
        entries.append(text[start:end].strip())
        position = end

    return entries


def add_author_fallback_from_editor(entry: str) -> tuple[str, bool]:
    """
    Add an author field to an editor-only entry for the web renderer.

    The original editor field remains unchanged. This only affects the
    generated web BibTeX file.
    """
    if AUTHOR_RE.search(entry):
        return entry, False

    editor_match = EDITOR_RE.search(entry)

    if editor_match is None:
        return entry, False

    header_match = re.match(
        r"""
        (?P<header>
            \s*
            @[A-Za-z]+
            \s*[\{\(]
            \s*[^,]+
            \s*,
        )
        """,
        entry,
        re.IGNORECASE | re.VERBOSE,
    )

    if header_match is None:
        return entry, False

    editor_value = editor_match.group("value")
    insertion_position = header_match.end()

    updated = (
        entry[:insertion_position]
        + "\n  author = "
        + editor_value
        + ","
        + entry[insertion_position:]
    )

    return updated, True


def publication_year(entry: str) -> int | None:
    """
    Return an entry's four-digit year, or None if no valid year is found.
    """
    match = YEAR_RE.search(entry)

    if match is None:
        return None

    return int(match.group("year"))


def sort_entries_newest_first(entries: list[str]) -> list[str]:
    """
    Sort entries by descending year.

    Python's sort is stable, so entries with the same year retain their
    original relative order. Entries without a valid year are placed last.
    """
    return sorted(
        entries,
        key=lambda entry: (
            publication_year(entry) is None,
            -(publication_year(entry) or 0),
        ),
    )


def count_publications(text: str) -> int:
    """
    Count bibliography records while excluding @string, @comment,
    and @preamble.
    """
    return len(ENTRY_START_RE.findall(text))


def main() -> None:
    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"Could not find {INPUT_FILE.resolve()}"
        )

    source = INPUT_FILE.read_text(encoding="utf-8")

    input_entries = count_publications(source)
    strings, cleaned = extract_strings_and_clean(source)
    expanded = expand_string_fields(cleaned, strings)

    entries = split_publication_entries(expanded)

    editor_fallbacks = 0
    web_entries: list[str] = []

    for entry in entries:
        updated_entry, fallback_added = add_author_fallback_from_editor(entry)
        web_entries.append(updated_entry)
        editor_fallbacks += int(fallback_added)

    sorted_entries = sort_entries_newest_first(web_entries)
    output_entries = len(sorted_entries)

    if input_entries == 0:
        raise RuntimeError(
            "No publication entries were found in myRefs.bib."
        )

    if output_entries != input_entries:
        raise RuntimeError(
            "Publication count changed during conversion: "
            f"{input_entries} input entries versus "
            f"{output_entries} output entries."
        )

    header = (
        "% This file is generated automatically from myRefs.bib.\n"
        "% Do not edit this file directly.\n"
        "% Entries are sorted from newest to oldest.\n"
        "% Editor-only entries receive an author fallback for the web renderer.\n\n"
    )

    output_text = header + "\n\n".join(sorted_entries) + "\n"

    OUTPUT_FILE.write_text(
        output_text,
        encoding="utf-8",
    )

    years = [
        publication_year(entry)
        for entry in sorted_entries
        if publication_year(entry) is not None
    ]

    year_range = (
        f"{max(years)}–{min(years)}"
        if years
        else "no valid years found"
    )

    print(
        f"Wrote {OUTPUT_FILE} with "
        f"{len(strings)} string definitions expanded "
        f"across {output_entries} publication entries."
    )
    print(f"Sorted entries newest first ({year_range}).")
    print(
        "Added author fallbacks from editor fields for "
        f"{editor_fallbacks} editor-only entries."
    )


if __name__ == "__main__":
    main()
