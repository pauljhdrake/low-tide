# EQ theme swatches in README

## Summary

- Added a generated gradient image (`assets/eq_themes.png`) showing all five EQ themes
  (classic, fire, ice, mono, neon) as vertical colour bars, bottom → top matching the
  actual visualiser rendering
- Image placed directly below the `eq_theme` config row in the README
- Image generated from the theme colour stops in `eq_visualizer.py` using Pillow

## Test plan

- [ ] Confirm `assets/eq_themes.png` renders correctly on the GitHub README page
- [ ] Confirm the image appears below the `eq_theme` row in the Configuration table
