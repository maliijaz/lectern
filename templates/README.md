# Templates

Themes are defined in code, in `backend/app/render/theme.py`, rather than shipped as binary
template files. That keeps the repository text-only and diffable, and avoids the
placeholder-mapping breakage that comes from reusing someone else's slide master.

This directory is where you override that with your school's own branding.

## `pptx/` — your institutional slide master

Drop a PowerPoint file here and decks will be drawn onto it, inheriting its master slide,
fonts, colours and logo:

- `pptx/default.pptx` — used for every deck
- `pptx/academic.pptx` — used only when the "Academic" theme is selected (any theme key works)

A theme-specific file wins over `default.pptx`. The content is still drawn as explicit text
boxes, so your template's layouts do not need to match anything in particular — it supplies
the background, the fonts and the branding, and nothing is silently dropped.

Make one by taking a normal presentation, deleting every slide, setting up the slide master
the way you want it, and saving it here.

## `typst/` — PDF document styling

PDFs are built from Typst source generated in `backend/app/render/pdf.py`, which applies the
same theme tokens as everything else. If you need a different page setup — a letterhead, a
different paper size, a required footer — edit `_preamble()` there. It is about forty lines
of Typst and the whole document compiles in a few hundred milliseconds, so iterating is fast.

## `docx/` — Word styling

Word documents are built with real Word styles (`Heading 1`, `List Bullet`, `Table Grid`),
set from the theme in `backend/app/render/docx.py`. That means a teacher can restyle a whole
document in Word with one click rather than fighting hard-coded formatting.

---

Files you add here are yours; the `.gitignore` does not exclude them, so a school can commit
its branding alongside the code.
