# Synthetic GRAY ICC fixtures

These authored synthetic profiles and PNGs are CC0 test material, not vendor
profiles or user images. The JSON records freeze source/profile SHA256 identities,
original samples and independent 60-digit Decimal analytical expected colors.

The oracle uses ICC v4.3 Annex F.2 forward kTRC times PCS D50 white, followed by
an independently inverted published rational sRGB / Bradford D65-to-D50 matrix,
sRGB encoding and one final half-up rounding. It does not import production code
or use native CMS outputs. Color tolerance is one code per channel; alpha and
black/white endpoints are exact. Separate negative and metadata fixtures exercise
local subset admission and precedence, not general ICC validity.
