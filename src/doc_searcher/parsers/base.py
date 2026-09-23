# Purpose: Abstract base class and data structures for document parsing.
# What the code does:
#   - Defines PageSegment to hold text and location (page, slide, or sheet).
#   - Defines ExtractedDoc to hold overall document metadata and extracted text segments.
#   - Defines ParseStatus, the stable outcome of every parse (success, empty, unsupported,
#     unreadable, encrypted, corrupt, dependency_missing), and ExtractedDoc.failed().
#   - Defines BaseParser interface with error-resilient parsing contract: parse() returns an
#     ExtractedDoc for expected failures instead of raising.
# Usage notes, dependencies, or assumptions:
#   - Inherited by all format-specific parsers.
#   - Pure Python dataclasses, no external dependencies required.

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional
from abc import ABC, abstractmethod


class ParseStatus(str, Enum):
    """Stable parse outcomes; values are safe to persist or show in diagnostics."""

    SUCCESS = "success"                        # text extracted
    EMPTY = "empty"                            # valid document without extractable text
    UNSUPPORTED = "unsupported"                # no parser for this extension
    UNREADABLE = "unreadable"                  # missing file or OS/permission error
    ENCRYPTED = "encrypted"                    # password required to read content
    CORRUPT = "corrupt"                        # file is readable but not a valid document
    DEPENDENCY_MISSING = "dependency_missing"  # parser library is not installed


@dataclass
class PageSegment:
    """Represents text extracted from a specific section of a document.
    
    Attributes:
        segment_id: Page number (1-based), Slide number, or Sheet name.
        segment_type: 'page', 'slide', 'sheet', or 'section'.
        text: Extracted plain text content.
    """
    segment_id: str
    segment_type: str
    text: str


@dataclass
class ExtractedDoc:
    """Represents the complete parsed document.
    
    Attributes:
        file_path: Absolute path to the source document.
        file_type: Extension or type tag (e.g. 'pdf', 'docx', 'xlsx').
        total_segments: Total number of pages/slides/sheets found.
        segments: List of PageSegment objects.
        error: Error message if parsing failed or was skipped (None on SUCCESS/EMPTY).
        status: Stable outcome category; SUCCESS/EMPTY are derived from segments when a
            parser does not report a failure.
    """
    file_path: str
    file_type: str
    total_segments: int = 0
    segments: List[PageSegment] = field(default_factory=list)
    error: Optional[str] = None
    status: Optional[ParseStatus] = None

    def __post_init__(self):
        if self.status is None:
            if self.error:
                self.status = ParseStatus.CORRUPT
            else:
                has_text = any(seg.text.strip() for seg in self.segments)
                self.status = ParseStatus.SUCCESS if has_text else ParseStatus.EMPTY

    @classmethod
    def failed(cls, file_path: str, file_type: str, status: ParseStatus, error: str) -> "ExtractedDoc":
        return cls(file_path=file_path, file_type=file_type, error=error, status=status)

    @classmethod
    def from_exception(cls, file_path: str, file_type: str, action: str, exc: BaseException) -> "ExtractedDoc":
        """Real OS errors (with an errno: missing file, permissions, I/O) are UNREADABLE.

        Libraries also raise errno-less OSError for bad content (olefile, zipfile), and
        everything else, so those count as CORRUPT.
        """
        is_os_failure = isinstance(exc, OSError) and exc.errno is not None
        status = ParseStatus.UNREADABLE if is_os_failure else ParseStatus.CORRUPT
        return cls.failed(file_path, file_type, status, f"{action}: {exc}")

    @property
    def full_text(self) -> str:
        """Returns concatenated text across all segments."""
        return "\n\n".join(seg.text for seg in self.segments if seg.text.strip())


class BaseParser(ABC):
    """Abstract base class for all document format parsers."""

    @abstractmethod
    def parse(self, file_path: str) -> ExtractedDoc:
        """Parse document and extract text by segments.
        
        Args:
            file_path: Absolute path to the file.
            
        Returns:
            ExtractedDoc containing segments and metadata.
        """
        pass
