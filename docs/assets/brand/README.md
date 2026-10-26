# Relayna brand assets

Selected, unmodified exports from the owner-supplied `@relayna/design-system`
0.4.0 package, supplied on 2026-10-26. Identity: Natural Connection / Mulberry &
Linen; Slim Leaf symbol; Open Signal campaign. Documentation, Studio and the
refreshed outlined logos/banners use official **Manrope 4.505**, variable weights
200–800. The approved SDK tagline remains “Your logic. Connected execution.”

`css/fonts.css` loads the exact upstream WOFF2 with `font-display: swap`.
System fallbacks handle loading and unsupported scripts. Use weight 400 for
body text, 500 for controls, 600 for headings, normal tracking and
`font-synthesis: none`; the upstream family has no italic face. Both sans and
code-role tokens select Manrope. Code preserves whitespace; semantic tables
and tabular numerals supply alignment without assuming equal character widths.

The site defaults to light regardless of system appearance. Material's moon/sun
toggle saves an explicit choice and restores it on subsequent visits. The
prepaint adapter aligns shared tokens before styles load; supplied light/dark
artwork follows the same explicit choice.

`manifest.json` records SHA-256 hashes for these unmodified exports. Font
revision, hashes and conversion evidence are in `provenance/manrope.json` and
`provenance/manrope-upstream.json`. Refresh from authoritative exports and keep
consumer CSS outside this directory. README and docs share the SDK banners.

Manrope is licensed under SIL Open Font License 1.1; its copyright and full
license are distributed in [fonts/OFL.txt](fonts/OFL.txt). The Relayna brand
assets remain private project material under the separate
[usage policy](USAGE-RIGHTS.md). The font license does not grant rights to the
brand artwork.
