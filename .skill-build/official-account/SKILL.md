---
name: official-account
description: Generate a Chinese WeChat Official Account paper interpretation from one PDF and six numbered figures in materials/. Use for requests to analyze paper materials and produce the DOI URL, guide, six original-number figure explanations, conclusion, short summary, ten titles, and structured outputs for later manual filling with fill_wechat_editor.py.
---

# Official Account

Create and validate article content from the current workspace's `materials/`. Do not operate a browser or run the WeChat filling script.

## Workflow

1. Inspect `materials/` recursively.
   - Require one paper PDF and exactly six PNG/JPEG/WebP images.
   - Extract each image's original figure number from names containing `grN`, `figN`, `figureN`, or `图N`.
   - Sort images numerically by original figure number.
   - Do not inspect image pixels unless the PDF and filenames cannot establish the mapping.
2. Extract the PDF text. Treat the paper as the only factual source.
   - Identify original title, Chinese title, DOI, journal abbreviation, corresponding-author affiliation and country.
   - Normalize DOI as `https://doi.org/<doi>` and verify it appears in the supplied PDF.
   - Determine the total main-text figure count from `Fig. N`/`Figure N` captions; use the largest main-text caption number.
3. Draft:
   - one-paragraph guide covering research background and principal work;
   - six separate paragraphs, each formatted `图N：……`, describing only the six supplied original figures;
   - one-paragraph principal conclusion;
   - one Chinese summary of at most 120 characters;
   - exactly ten accurate, non-hyped titles, each at least 25 Chinese characters.
4. Format titles deterministically:
   - Chinese corresponding affiliation: `机构名 | 刊名缩写 | 描述`;
   - non-Chinese corresponding affiliation: `刊名缩写 | 描述`.
5. Save structured results to `outputs/metadata.json`, preserving the schema in [references/article-schema.md](references/article-schema.md). Also write `outputs/article.md` for review.
6. Validate the generated files, then stop. The user will run `fill_wechat_editor.py` manually in their configured PyCharm virtual environment.

## Quality gates

- Never invent missing paper facts, figure numbers, affiliations, results, or conclusions.
- Treat `doi_url` as required; report a clear error rather than guessing when the PDF contains no DOI.
- Keep original figure numbers; never renumber the six selected figures as 1–6.
- Fill the figure summary exactly as `原文共有图N，本文图片选自原文中图a、b、c、d、e、f`.
- Do not launch Chrome, call browser-control tools, run `fill_wechat_editor.py`, or attempt to fill/save/publish a WeChat article.
