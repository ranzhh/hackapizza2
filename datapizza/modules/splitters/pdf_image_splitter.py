import logging
import uuid
from pathlib import Path
from typing import Literal

from datapizza.core.modules.splitter import Splitter
from datapizza.type.type import Chunk

log = logging.getLogger(__name__)


class PDFImageSplitter(Splitter):
    """Splits a PDF document into individual pages, saves each page as an image using fitz,
    and returns metadata about each page as a Chunk object.
    """

    def __init__(
        self,
        image_format: Literal["png", "jpeg"] = "png",
        output_base_dir: str | Path = "output_images",
        dpi: int = 300,  # Added DPI setting for fitz
    ):
        """Initializes the Splitter.

        Args:
            image_format: The format to save the images in ('png' or 'jpeg'). Defaults to 'png'.
            output_base_dir: The base directory where images for processed PDFs will be saved.
                              A subdirectory will be created for each PDF. Defaults to 'output_images'.
            dpi: Dots Per Inch for rendering the PDF page to an image. Higher values increase resolution and file size. Defaults to 300.
        """
        self.image_format = image_format.lower()
        if self.image_format not in [
            "png",
            "jpeg",
            "jpg",
        ]:  # Allow jpg as alias for jpeg
            # Fitz save uses extension, so jpg is fine. Allow it as alias.
            if self.image_format == "jpg":
                self.image_format = "jpeg"
            else:
                raise ValueError("image_format must be 'png' or 'jpeg'/'jpg'")
        self.output_base_dir = Path(output_base_dir)
        self.dpi = dpi

    def split(self, pdf_path: str | Path) -> list[Chunk]:
        """Processes the PDF using fitz: converts pages to images and returns Chunk objects.

        Args:
            pdf_path: The path to the input PDF file.

        Returns:
            A list of Chunk objects, one for each page of the PDF.
        """

        try:
            import fitz
        except ImportError as e:
            raise ImportError(
                "PyMuPDF is not installed. Please install it using `pip install PyMuPDF`."
            ) from e

        pdf_path = Path(pdf_path)
        if not pdf_path.is_file() or pdf_path.suffix.lower() != ".pdf":
            raise ValueError(f"Invalid PDF path: {pdf_path}")

        # Create a unique output directory for this PDF's images
        pdf_filename = pdf_path.stem
        pdf_output_dir = self.output_base_dir / pdf_filename
        pdf_output_dir.mkdir(parents=True, exist_ok=True)

        chunks = []
        doc = None  # Initialize doc to None
        try:
            doc = fitz.open(pdf_path)
            for i, page in enumerate(doc):  # type: ignore
                page_num = i + 1
                image_id = str(uuid.uuid4())
                # Use 'jpg' extension if format is 'jpeg' for compatibility with fitz save
                file_extension = (
                    "jpg" if self.image_format == "jpeg" else self.image_format
                )
                image_filename = f"page_{page_num}_{image_id}.{file_extension}"
                image_path = pdf_output_dir / image_filename

                try:
                    # Generate pixmap with specified DPI
                    pix = page.get_pixmap(dpi=self.dpi)
                    # Save the pixmap. Fitz determines format from the extension.
                    pix.save(str(image_path))
                except Exception as e:
                    # Handle potential errors during pixmap generation or saving
                    print(
                        f"Error processing page {page_num} of {pdf_path} to {image_path}: {e}"
                    )
                    # Decide on error handling: skip this page, raise an error, etc.
                    # For now, we'll skip this page and continue
                    continue  # Skip creating a chunk for this failed page

                chunk = Chunk(
                    id=image_id,
                    text="",  # As requested
                    embeddings=[],  # Default factory handles this, but being explicit
                    metadata={
                        "image_path": str(image_path.resolve())
                    },  # Store absolute path
                )
                chunks.append(chunk)

        except Exception as e:
            log.error(f"Error opening or processing PDF {pdf_path}: {e}")
            raise Exception(f"Failed to process PDF {pdf_path}.") from e
        finally:
            if doc:
                doc.close()  # Ensure the document is closed

        return chunks

    async def a_split(self, pdf_path: str | Path) -> list[Chunk]:
        return self.split(pdf_path)

    def __call__(self, pdf_path: str | Path) -> list[Chunk]:
        return self.split(pdf_path)
