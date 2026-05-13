from pathlib import Path

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"}
VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".avi"}
TABLE_EXTENSIONS = {".csv", ".tsv", ".xlsx", ".xls", ".xlsm", ".xlsb", ".ods"}
CODE_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".jsx",
    ".tsx",
    ".java",
    ".cpp",
    ".c",
    ".html",
    ".css",
    ".json",
    ".xml",
    ".md",
}
FORMULA_EXTENSIONS = {".tex"}
PDF_EXTENSIONS = {".pdf"}

ALL_SUPPORTED_EXTENSIONS = (
    IMAGE_EXTENSIONS
    | VIDEO_EXTENSIONS
    | TABLE_EXTENSIONS
    | CODE_EXTENSIONS
    | FORMULA_EXTENSIONS
    | PDF_EXTENSIONS
)

CATEGORY_BY_EXTENSION = {
    **{ext: "image" for ext in IMAGE_EXTENSIONS},
    **{ext: "video" for ext in VIDEO_EXTENSIONS},
    **{ext: "table" for ext in TABLE_EXTENSIONS},
    **{ext: "code" for ext in CODE_EXTENSIONS},
    **{ext: "formula" for ext in FORMULA_EXTENSIONS},
    **{ext: "pdf" for ext in PDF_EXTENSIONS},
}

LANGUAGE_BY_EXTENSION = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".jsx": "javascript",
    ".tsx": "typescript",
    ".java": "java",
    ".cpp": "cpp",
    ".c": "c",
    ".html": "html",
    ".css": "css",
    ".json": "json",
    ".xml": "xml",
    ".md": "markdown",
    ".tex": "latex",
}


def normalized_extension(filename: str) -> str:
    return Path(filename).suffix.lower().strip()
