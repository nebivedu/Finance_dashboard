## Finance Dashboard Style Guide

### Typography
- Base font: `'Courier New', Courier, monospace` for the entire UI.
- Headings (`h1`, `h2`): uppercase, heavy weight (900), large sizes (48px for `h1`, 30px for `h2`), negative letter-spacing for a vintage typewriter feel.
- Summary metric values use 26px bold text; badge numbers use 12px bold uppercase text.

### Layout
- Body padding: `30px 20px 40px`.
- Container width: fixed `max-width: 1300px; margin: 0 auto`.
- Cards: white background, `5px solid #000` border, `25px` padding, `25px` bottom margin, slightly rounded corners for summary tiles.
- Navigation bar: sticky (`top: 10px`, `z-index: 100`), same border as cards, white background, uppercase links separated by black dividers.
- Monthly summary is a full-width accordion (HTML `<details>` panels) where the summary row shows income/expense/net and expanding reveals the month's transactions.

### Colors
- Background: `#fffef7` (soft cream).
- Text: black.
- Summary tiles:
  - Income: `#c8e6c9`.
  - Expenses: `#ffcdd2`.
  - Net: `#bbdefb`.
  - Transaction count: `#fff4c3`.
  - Uncategorized: `#f7d8ae` with badge background `#d89a50`.
- Transaction amount pills: green for positives (`#c8e6c9`) and red for negatives (`#ffcdd2`).

### Navigation / Links
- Links use uppercase bold text, default black with underline for inline links, and fill background on hover (`#e1bee7`).

### Tables
- Full-width, `3px solid #000` grid, `10px-14px` padding depending on page.
- Headers: black background, white text, uppercase.

### Charts
- Chart.js line chart with green income line (`#2d5f3f`, translucent fill `rgba(200,230,201,0.5)`) and red expense line (`#7f2c2c`, translucent fill `rgba(255,205,210,0.5)`).
- Pie charts use pastel palette (pinks, purples, blues, greens) with black border.

### Components
- Summary grid uses 5-column CSS grid with badge indicator for uncategorized count.
- Transaction table columns sized to keep descriptions wide and ID/date columns narrow; forms inline for category assignment.

Use this file as a reference when adding new pages or components to keep the retro Courier-on-cream aesthetic consistent.
