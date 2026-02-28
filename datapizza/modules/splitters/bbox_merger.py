from datapizza.core.models import PipelineComponent
from datapizza.type import Chunk


class BboxMerger(PipelineComponent):
    def __init__(self):
        """
        Initialize the BboxMerger
        """
        pass

    @staticmethod
    def get_combined_bounding_boxes(coordinates):
        """
        Given a list of bounding boxes with their page numbers, returns either:
        - A single dictionary with 'pageNumber' and 'polygon' keys if all boxes are on the same page
        - A list of dictionaries with 'pageNumber' and 'polygon' keys if they span multiple pages

        Args:
            coordinates: List of dictionaries containing 'pageNumber' and 'polygon' keys
                Example: [{'pageNumber': 1, 'polygon': [x1, y1, x2, y2, ...]}, ...]

        Returns:
            If single page: {'pageNumber': page_num, 'polygon': [x1, y1, x2, y2, ...]}
            If multiple pages: List of {'pageNumber': page_num, 'polygon': [x1, y1, x2, y2, ...]} for each page
        """
        # Group coordinates by page number
        page_coords = {}
        for coord in coordinates:
            page_num = coord["pageNumber"]
            polygon = coord["polygon"]
            if page_num not in page_coords:
                page_coords[page_num] = []
            page_coords[page_num].append(polygon)

        # Calculate bounding box for each page
        page_bboxes = {}
        for page_num, coords_list in page_coords.items():
            # Initialize with first box
            first_coords = coords_list[0]
            min_x = min(first_coords[::2])
            min_y = min(first_coords[1::2])
            max_x = max(first_coords[::2])
            max_y = max(first_coords[1::2])

            # Update with remaining boxes
            for coords in coords_list[1:]:
                min_x = min(min_x, min(coords[::2]))
                min_y = min(min_y, min(coords[1::2]))
                max_x = max(max_x, max(coords[::2]))
                max_y = max(max_y, max(coords[1::2]))

            # Create polygon with 4 corners in clockwise order: top-left, top-right, bottom-right, bottom-left
            combined_polygon = [
                min_x,
                min_y,  # top-left
                max_x,
                min_y,  # top-right
                max_x,
                max_y,  # bottom-right
                min_x,
                max_y,  # bottom-left
            ]

            page_bboxes[page_num] = {
                "pageNumber": page_num,
                "polygon": combined_polygon,
            }

        # If all boxes are on the same page, return single bbox
        if len(page_bboxes) == 1:
            return next(iter(page_bboxes.values()))

        # Otherwise return list of bboxes ordered by page number
        return [page_bboxes[page] for page in sorted(page_bboxes.keys())]

    def merge_metadata(self, chunks: list[Chunk]) -> list[Chunk]:
        for chunk in chunks:
            if chunk.metadata.get("boundingRegions"):
                chunk.metadata["boundingRegions"] = self.get_combined_bounding_boxes(
                    chunk.metadata["boundingRegions"]
                )
        return chunks

    def __call__(self, chunks: list[Chunk]) -> list[Chunk]:
        return self.merge_metadata(chunks)

    async def _a_run(self, chunks: list[Chunk]) -> list[Chunk]:
        return self.merge_metadata(chunks)
