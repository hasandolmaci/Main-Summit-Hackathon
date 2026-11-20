# Sunbeam UI: Liquid Glass kit

Style 06 ("Liquid Glass") from the crystal button exploration, built as a small reusable kit plus a demo shortlist page. Plain HTML, CSS and JS, no build step.

Open `index.html` in a browser to see it. Everything on the demo page is sample data with made-up companies.

## Files

| File | What it is |
|---|---|
| `liquid-glass.css` | Tokens and components. Light by default, dark via `prefers-color-scheme` or `data-theme="dark"` on `<html>`. |
| `liquid-glass.js` | Optional helpers: the highlight follows the pointer, and `.lg-seg` toggles `aria-pressed` and fires `lg-change`. |
| `shortlist.sample.js` | Sample radar output (`window.SUNBEAM_SAMPLE`). Swap in the real run output with the same shape. |
| `index.html` | Demo page: top bar, run summary, search and sector filter, shortlist cards. |

## Components

| Class | Use |
|---|---|
| `.lg-ambient` | Put on `body`. Paints the coloured backdrop the glass refracts. Change `--lg-ambient-1..4` to recolour it. |
| `.lg-glass` | The glass material. Add it to any container. |
| `.lg-btn.lg-btn--primary` / `--secondary` | Buttons. Hover swells on a spring, press squishes wide. `aria-busy="true"` shows a running state. `--sm` and `--icon` sizes. |
| `.lg-card.lg-glass` | Shortlist row. Inside: `.lg-row-head`, `.lg-rank`, `.lg-name`, `.lg-score`, `.lg-meter` (set `--s` from 0 to 1), `.lg-chips` / `.lg-chip`, `.lg-twin`. |
| `.lg-panel.lg-glass` | Larger container. `.lg-panel--bar` makes a pill-shaped top bar. |
| `.lg-input` | Text and search inputs. |
| `.lg-seg.lg-glass` | Segmented control of `<button aria-pressed>`. |

## Shortlist row data

```js
{ name, place, what, sector: 'deeptech' | 'software', score: 0..1,
  reasons: [[code, text], [code, text], [code, text]],   // code: PAT, GRANT, TM, GH
  twin: 'Most similar past winner' }
```

## Notes

- Glass blur needs `backdrop-filter`, which current Chrome, Safari, Edge and Firefox support. Without it, the glass falls back to a denser tint so text stays readable.
- Motion is switched off for people who prefer reduced motion.
- The brand colours and logo are placeholders until the logo and colour work is final. Only tokens need to change.
