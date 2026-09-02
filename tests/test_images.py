import io

import messages
from PIL import Image

from emailmcp.imap import images


def test_every_image_is_decoded_downscaled_and_re_encoded():
    big = images.render(messages.png(3000, 1500))
    assert (big.width, big.height) == (images.MAX_DIMENSION, images.MAX_DIMENSION // 2)
    assert Image.open(io.BytesIO(big.data)).size == (big.width, big.height)
    assert big.mime_type == "image/jpeg"

    small = images.render(messages.png(200, 120))
    assert (small.width, small.height) == (200, 120)

    with_alpha = io.BytesIO()
    Image.new("RGBA", (200, 120), (10, 20, 30, 0)).save(with_alpha, format="PNG")
    transparent = images.render(with_alpha.getvalue())
    assert transparent.mime_type == "image/png"
    assert Image.open(io.BytesIO(transparent.data)).mode == "RGBA"

    # Decoding is also how a mislabelled part is caught.
    assert images.render(b"%PDF-1.4 not an image") is None


def test_decorativeness_is_judged_on_the_source_not_the_downscaled_copy():
    """A wide banner shrinks to a few dozen pixels tall. Measured afterwards it
    looks exactly like a spacer, and the chart in it disappears."""
    banner = images.render(messages.png(4000, 120))
    assert (banner.width, banner.height) == (1568, 47)
    assert banner.decorative is False

    assert images.render(messages.png(1, 1)).decorative is True
    assert images.render(messages.png(600, 4)).decorative is True
    assert images.render(messages.png(600, 400)).decorative is False

    # A flat 400x400 PNG compresses to well under the source threshold.
    flat = io.BytesIO()
    Image.new("RGB", (400, 400), (255, 255, 255)).save(flat, format="PNG")
    assert len(flat.getvalue()) < images.MIN_SOURCE_BYTES
    assert images.render(flat.getvalue()).decorative is True


def test_a_budget_stops_at_whichever_ceiling_comes_first():
    image = images.render(messages.png(200, 120))

    by_count = images.Budget(max_images=2)
    assert [by_count.take(image) for _ in range(3)] == [True, True, False]

    by_size = images.Budget(max_bytes=len(image.data))
    assert [by_size.take(image) for _ in range(2)] == [True, False]
