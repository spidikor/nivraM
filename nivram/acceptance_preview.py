"""Readiness and typed evidence for the synthetic exact-PNG acceptance check."""
from __future__ import annotations

import math


# Image.complete can become true before profiles.mjs resumes after image.decode.
# That continuation selects pixel view, then enables Save this PNG. Wait for the
# application to finish that operation before measuring its initialized scale.
PREVIEW_SNAPSHOT = """(() => {
    const dialog=document.getElementById('png-preview-dialog');
    const image=document.getElementById('png-preview-image');
    const saves=dialog ? [...dialog.querySelectorAll('button')].filter(
        button=>button.textContent==='Save this PNG') : [];
    if(!dialog?.open || !image?.complete || !image.currentSrc ||
       image.naturalWidth!==2000 || image.naturalHeight<=0 ||
       saves.length!==1 || saves[0].disabled)return false;
    const inlineWidth=image.style.width;
    const displayedWidth=Number.parseFloat(inlineWidth);
    const devicePixelRatio=window.devicePixelRatio;
    if(!inlineWidth.endsWith('px') || !Number.isFinite(displayedWidth) ||
       displayedWidth<=0 || !Number.isFinite(devicePixelRatio) || devicePixelRatio<=0)return false;
    const canvas=document.createElement('canvas');canvas.width=1;canvas.height=1;
    const context=canvas.getContext('2d');context.drawImage(image,0,0);
    return {width:image.naturalWidth,height:image.naturalHeight,
        corner:Array.from(context.getImageData(0,0,1,1).data),displayedWidth,devicePixelRatio};
})()"""


def _finite_positive(value):
    return type(value) in (int, float) and 0 < value <= 10**9


def preview_observation(value):
    """Keep only bounded numeric/type evidence, never arbitrary returned text."""
    observation = {'type': type(value).__name__[:80]}
    if type(value) is not dict:
        return observation
    for key in ('width', 'height', 'displayedWidth', 'devicePixelRatio'):
        item = value.get(key)
        if type(item) in (int, float) and abs(item) <= 10**9:
            observation[key] = item
        elif item is None:
            observation[key] = None
        else:
            observation[key + 'Type'] = type(item).__name__[:80]
    corner = value.get('corner')
    if type(corner) is list and len(corner) == 4 and all(type(v) is int and 0 <= v <= 255 for v in corner):
        observation['corner'] = list(corner)
    else:
        observation['cornerType'] = type(corner).__name__[:80]
    return observation


def validate_preview_snapshot(value):
    """Reject transport nulls/malformed values before performing pixel math."""
    if type(value) is not dict:
        raise AssertionError('The exact PNG preview returned no structured snapshot.')
    width, height = value.get('width'), value.get('height')
    if type(width) is not int or width != 2000 or type(height) is not int or not 0 < height <= 32768:
        raise AssertionError('The exact PNG preview returned invalid image dimensions.')
    displayed, ratio = value.get('displayedWidth'), value.get('devicePixelRatio')
    if not _finite_positive(displayed) or not _finite_positive(ratio):
        raise AssertionError('The exact PNG preview pixel scale is not finite and positive.')
    corner = value.get('corner')
    if type(corner) is not list or len(corner) != 4 or any(type(v) is not int or v != 255 for v in corner):
        raise AssertionError('The exact PNG preview did not preserve opaque white corners.')
    scaled = displayed * ratio
    if not math.isfinite(scaled) or abs(scaled - width) > .01:
        raise AssertionError('Pixel inspection does not map image pixels to physical screen pixels.')
    return {'width': width, 'height': height, 'corner': list(corner),
            'displayedWidth': displayed, 'devicePixelRatio': ratio}
