"""
image_utils.py

Utilities for image manipulation.
"""

import logging
import os
from PIL import Image, ImageOps
from typing import Optional

logger = logging.getLogger(__name__)

def prepare_chart_image(
    image_path: str, 
    top_percent: float = 15.0,  # Default top crop
    bottom_percent: float = 30.0, # Default bottom crop
    border_size: int = 2,
    border_color: str = "black"
) -> Optional[str]:
    """
    Prepares a chart image by cropping, splitting into 4 quadrants, 
    adding borders to each, and combining them vertically.

    Args:
        image_path: Path to the input image file.
        top_percent: Percentage of height to crop from the top initially (0-100).
        bottom_percent: Percentage of height to crop from the bottom initially (0-100).
        border_size: Pixel size of the border to add around each quadrant.
        border_color: Color of the border.

    Returns:
        The path to the modified (prepared) image, or None if processing failed.
        Note: This function modifies the image in-place.
    """
    # --- 1. Initial Validation --- 
    if top_percent < 0 or top_percent >= 100 or bottom_percent < 0 or bottom_percent >= 100:
        logger.error(f"Invalid initial crop percentages: top={top_percent}, bottom={bottom_percent}. Must be < 100.")
        return None
    if top_percent + bottom_percent >= 100:
         logger.error(f"Total initial crop percentage ({top_percent + bottom_percent}%) cannot be 100% or more.")
         return None
    if border_size < 0:
        logger.error(f"Invalid border size: {border_size}. Must be non-negative.")
        return None

    try:
        logger.info(f"Preparing chart image: {image_path}")
        with Image.open(image_path) as img:
            original_width, original_height = img.size
            logger.debug(f"Original dimensions: {original_width}x{original_height}")

            # --- 2. Initial Top/Bottom Cropping --- 
            top_pixels = int(original_height * (top_percent / 100.0))
            bottom_pixels = int(original_height * (bottom_percent / 100.0))
            initial_crop_box = (0, top_pixels, original_width, original_height - bottom_pixels)
            
            if initial_crop_box[1] >= initial_crop_box[3] or initial_crop_box[0] >= initial_crop_box[2]:
                logger.error(f"Invalid initial crop dimensions for {image_path}. Cannot crop.")
                return None

            cropped_img = img.crop(initial_crop_box)
            cropped_width, cropped_height = cropped_img.size
            logger.debug(f"Dimensions after initial crop: {cropped_width}x{cropped_height}")

            # --- 3. Split into 4 Quadrants --- 
            mid_x = cropped_width // 2
            mid_y = cropped_height // 2

            # Ensure midpoints allow for non-zero quadrant dimensions
            if mid_x <= 0 or mid_y <= 0:
                logger.error(f"Cannot split into quadrants: Cropped image dimensions too small ({cropped_width}x{cropped_height}).")
                return None

            tl_img = cropped_img.crop((0, 0, mid_x, mid_y))
            tr_img = cropped_img.crop((mid_x, 0, cropped_width, mid_y))
            bl_img = cropped_img.crop((0, mid_y, mid_x, cropped_height))
            br_img = cropped_img.crop((mid_x, mid_y, cropped_width, cropped_height))
            logger.debug(f"Split into 4 quadrants.")

            # --- 4. Add Borders --- 
            quadrants = [tl_img, tr_img, bl_img, br_img]
            bordered_quadrants = []
            if border_size > 0:
                for i, quad in enumerate(quadrants):
                    try:
                        bordered_quad = ImageOps.expand(quad, border=border_size, fill=border_color)
                        bordered_quadrants.append(bordered_quad)
                    except Exception as border_err:
                        logger.error(f"Error adding border to quadrant {i}: {border_err}", exc_info=True)
                        return None # Fail preparation if bordering fails
                logger.debug(f"Added {border_size}px {border_color} border to quadrants.")
            else:
                bordered_quadrants = quadrants # Use original quadrants if no border needed
            
            # --- 5. Combine Vertically --- 
            # Use the width of the first bordered quadrant (they should all be the same)
            combined_width = bordered_quadrants[0].width 
            combined_height = sum(q.height for q in bordered_quadrants)
            
            # Create a new image canvas (use RGBA if original had alpha, otherwise RGB)
            combined_mode = img.mode if img.mode in ['RGB', 'RGBA'] else 'RGB'
            combined_image = Image.new(combined_mode, (combined_width, combined_height), 'white') 
            logger.debug(f"Created combined canvas: {combined_width}x{combined_height}")
            
            current_y = 0
            for quad in bordered_quadrants:
                combined_image.paste(quad, (0, current_y))
                current_y += quad.height
            logger.debug(f"Pasted 4 quadrants vertically.")

            # --- 6. Add Final Outer Border --- 
            final_border_size = 50 # Let's use 10 pixels for the outer border
            if final_border_size > 0:
                 try:
                     final_image_with_border = ImageOps.expand(combined_image, border=final_border_size, fill=border_color)
                     logger.debug(f"Added final {final_border_size}px {border_color} border.")
                     image_to_save = final_image_with_border
                 except Exception as final_border_err:
                      logger.error(f"Error adding final outer border: {final_border_err}", exc_info=True)
                      image_to_save = combined_image # Fallback to saving the combined image without outer border
            else:
                 image_to_save = combined_image

            # --- 7. Save Final Image --- 
            # Overwrite the original file path with the final result
            image_to_save.save(image_path)
            logger.info(f"Prepared chart image saved successfully: {image_path}")
            
        return image_path # Return the path to the modified image
        
    except FileNotFoundError:
        logger.error(f"Image preparation failed: File not found at {image_path}")
        return None
    except Exception as e:
        logger.error(f"Error preparing chart image {image_path}: {str(e)}", exc_info=True)
        return None 