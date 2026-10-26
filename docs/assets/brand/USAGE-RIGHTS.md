# Usage and provenance

The Relayna brand assets and package are private project material. The selected Slim Leaf symbol and Mulberry & Linen identity remain Relayna's supplied artwork; no public license is selected for those assets or the package code.

**Manrope is third-party font software**, designed by Mikhail Sharanda and distributed by the Manrope Project Authors under **SIL Open Font License 1.1**. Its original copyright and full license are included in `source/manrope/OFL.txt` and `dist/fonts/OFL.txt`. This font license is independent of the private Relayna package metadata. Keep the copyright and OFL with every font distribution. Outlined logo and banner artwork is created with the font; it does not change the font's license or make the private brand publicly licensed.

The package ships the official Google Fonts variable TTF and a lossless WOFF2 copy. No outlines, spacing, names or additional styles were designed for this adoption. `source/manrope/upstream.json` pins the source revision; `provenance/manrope.json` records hashes and verification. Source: [Manrope specimen](https://fonts.google.com/specimen/Manrope), [Google Fonts source and OFL](https://github.com/google/fonts/tree/main/ofl/manrope).

Optional FontTools, HarfBuzz and CairoSVG build tools retain their own licenses and are not vendored. The package remains `private: true` and `UNLICENSED` for its own code/brand; that metadata does not override Manrope's OFL.
