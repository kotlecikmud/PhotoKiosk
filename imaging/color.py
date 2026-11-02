"""ICC Color profile extraction, preservation, and conversion management."""
from typing import Optional, Tuple
from PIL import Image, ImageCms
import io


def extract_icc_profile(image: Image.Image) -> Optional[bytes]:
    """Extract embedded ICC profile bytes from PIL Image if present."""
    icc = image.info.get("icc_profile")
    if icc and isinstance(icc, bytes) and len(icc) > 0:
        return icc
    return None


def get_icc_profile_info(icc_bytes: Optional[bytes]) -> dict:
    """Return descriptive metadata about an ICC profile."""
    if not icc_bytes:
        return {"has_profile": False, "description": "None (sRGB assumed)"}

    try:
        profile = ImageCms.getProfileDescription(io.BytesIO(icc_bytes))
        return {
            "has_profile": True,
            "description": str(profile).strip(),
            "size_bytes": len(icc_bytes),
        }
    except Exception as e:
        return {
            "has_profile": True,
            "description": f"Embedded profile ({len(icc_bytes)} bytes)",
            "error": str(e),
        }


def apply_color_management(
    image: Image.Image,
    source_icc: Optional[bytes] = None,
    target_icc_path: Optional[str] = None
) -> Image.Image:
    """
    Stage A & B Color Management Pipeline:
    - Stage A: Preserves source profile without destructive conversion.
    - Stage B (Future printer integration): If target_icc_path is provided,
      converts image using LittleCMS (ImageCms.buildTransform).
    """
    if target_icc_path and source_icc:
        try:
            input_profile = ImageCms.ImageCmsProfile(io.BytesIO(source_icc))
            output_profile = ImageCms.ImageCmsProfile(target_icc_path)
            # Transform image in-place or return transformed copy
            transformed = ImageCms.profileToProfile(
                image,
                input_profile,
                output_profile,
                renderingIntent=ImageCms.Intent.PERCEPTUAL,
                outputMode='RGB'
            )
            return transformed
        except Exception:
            # Fallback to unmodified image if color transform fails
            return image
    return image
