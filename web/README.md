# web

Next.js front end for the options analytics API.

```bash
npm install
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 npm run dev
```

The API must be running separately (`cd ../api && uvicorn options_tool.api.main:app --reload`).

## Notes

- `lib/payoff.ts` is a **second implementation** of the payoff maths that already
  exists in Python, kept so the spread builder redraws instantly while editing a
  leg. The API remains the source of truth. Both are tested against the same
  golden fixture (`../api/tests/fixtures/payoff_golden.json`), so they cannot
  drift apart without `npm run test:run` going red.
- Nullable analytics stay nullable all the way to the screen. `lib/format.ts`
  renders a missing value as an em dash; nothing coalesces null to zero, because
  a zero IV rank reads as "volatility is at its lows" and a null one means "not
  enough history yet".
