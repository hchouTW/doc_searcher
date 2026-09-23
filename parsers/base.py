# Purpose: Abstract base class and data structures for document parsing.
# What the code does:
#   - Defines PageSegment to hold text and location (page, slide, or sheet).
#   - Defines ExtractedDoc to hold overall document metadata and extracted text segments.
#   - Defines BaseParser interface with error-resilient parsing contract.
# Usage notes, dependencies, or assumptions:
#   - Inherited by all format-specific parsers.
#   - Pure Python dataclasses, no external dependencies required.

from dataclasses import dataclass, field
from typing import List, Optional
from abc import ABC, abstractmethod


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
        error: Error message if parsing partially failed or was skipped.
    """
    file_path: str
    file_type: str
    total_segments: int = 0
    segments: List[PageSegment] = field(default_factory=list)
    error: Optional[str] = None

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
