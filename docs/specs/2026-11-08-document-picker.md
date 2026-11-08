# Document Picker, Design

**Problem:** the document-id field in `PublishBar.vue` uses a native `<datalist>`. The browser draws that popup, and Chrome/Edge place it off to the right of the field and narrower than it, especially with page zoom or Windows display scaling above 100%. Our CSS can't position or style it.

**Goal:** a dropdown that always opens directly below the field, at the same width, and looks the same in every browser. The behaviour in spec section 7 (Publish row) stays: pick from the list or type any id; the list reloads on focus.

## Component: `web/src/components/DocumentPicker.vue`

A small combobox (an input plus a list we draw ourselves).

**API**
- `defineModel<string>({ required: true })`: the typed or picked document id. `required` keeps the type `string`, not `string | undefined`.
- Props:
  - `options: SourceDocument[]`: the documents to offer (PublishBar passes `unpublished`);
  - `id`, `placeholder`, `required`: go on the input;
  - `labelId`: names the listbox.
- Emits `focus`: PublishBar reloads the document list on it, as today.

**Input attributes:** `autocomplete="off"` and `spellcheck="false"`. Without `autocomplete="off"`, Chrome and Edge show their own saved-entries popup on top of ours.

**State**
- `open`: whether the user has the list open.
- `filtered`: the options whose id or filename contains the typed text (`trim()`ed, ignoring case).
- `expanded = open && filtered.length > 0`: drives both the list's `v-show` and `aria-expanded`.
- `highlightedId`: the document id of the highlighted option, or `null`.
  - It's tracked by id, not by index, because `options` is rebuilt every 2 s while the job list polls, and on each reload-on-focus. A rebuilt list that still holds the id keeps the highlight.
  - It's set to `null` on every `input` event and whenever the list closes. Typing hands control back to the text, so Enter can't pick an id the user didn't type.
- `highlightedIndex`: computed, the position of `highlightedId` in `filtered`, or -1. It's the single source for Enter, `aria-activedescendant` and `aria-selected`, so an id that has dropped out of `filtered` can never be picked. No watcher is needed.

**Behaviour**
- **Opens** on focus, on click and on typing. **Closes** on blur, Escape, Enter, and after a pick.
- **No matches:** `expanded` is false, so the list hides. A typed id that isn't listed is still allowed (spec: any id can be typed).
- **Mouse:**
  - `mousedown.prevent` sits on the listbox, so neither a press on an option nor a drag of its scrollbar blurs the input.
  - A click on an option picks it.
  - Moving the mouse over an option (`mousemove`, not `mouseenter`) highlights it. `mousemove` fires only on real pointer movement, so a list scrolled under a still pointer doesn't steal the highlight.
- **Keyboard** (ignored while `event.isComposing` or `keyCode === 229`, so IME input works, Safari included):
  - ArrowDown when `!expanded`: sets `open = true` and highlights `filtered[0]`, if there is one. When expanded: moves down one, stopping at the last.
  - ArrowUp: moves up one, stopping at the first. With nothing highlighted, it does nothing.
  - Both arrows call `preventDefault()`, so the text cursor doesn't jump.
  - Enter always closes the list. With a highlighted option, it also picks it and calls `preventDefault()`, so the form isn't submitted. With nothing highlighted, the browser submits the form.
  - Escape closes the list.
  - The highlighted option scrolls into view with `el.scrollIntoView?.({ block: 'nearest' })`. The `?.` is there because jsdom lacks the method.
- **Each option** shows the id in bold and the filename below it, as the current popup does.

**Accessibility (WAI-ARIA combobox pattern)**
- Input:
  - `role="combobox"`, `aria-autocomplete="list"`;
  - `aria-controls` set to `${baseId}-listbox`;
  - `aria-expanded` set to `expanded`;
  - `aria-activedescendant` set to `${baseId}-option-${highlightedIndex}`, or removed when the index is -1.
- List: `role="listbox"`, id `${baseId}-listbox`, always rendered (hidden with `v-show`), and named by `aria-labelledby` set to `labelId`.
- Options: `role="option"`, id `${baseId}-option-${i}`, with `aria-selected="true"` only on the highlighted one.
- `baseId` comes from Vue's `useId()`. The document id isn't used in element ids, because it may contain odd characters.
- The label uses `for`/`id` instead of wrapping the field. Otherwise the option text would become part of the field's accessible name.

**Layout (scoped CSS)**
- Wrapper: `position: relative`. It sizes to the field, as the input does today.
- Input: `width: 100%; box-sizing: border-box`. Without border-box, the 8px padding and 1px border make it wider than the wrapper, and so wider than the list.
- List:
  - `position: absolute; top: 100%; left: 0; right: 0; box-sizing: border-box`, with a small gap below the field;
  - `max-height` of about 6 options with `overflow-y: auto`, and a `z-index` above the cards;
  - the existing colour tokens (`--card`, `--border`, `--accent`).
- Only `[aria-selected="true"]` is highlighted, with no `:hover` style, so there's never a second "current" option.
- Filenames use `overflow-wrap: anywhere`, so long names wrap inside the list.

## Changes to `PublishBar.vue`

- Replace the `<input list>` and `<datalist>` with `<DocumentPicker>`.
- Markup: `<div class="field"><label id="document-id-label" for="document-id">Document id</label><DocumentPicker id="document-id" label-id="document-id-label" … /></div>`.
- The "Reloads on focus…" comment moves to `<DocumentPicker @focus>`.
- The rest stays: the Publish and Publish all buttons, the error messages, reload-on-focus, and clearing the field after a publish.

## Changes to `style.css`

One new rule, which keeps today's look now that the field sits outside its label: `.field { display: flex; flex-direction: column; gap: 4px; font-size: 0.9rem; }`. The input still inherits 0.9rem through `font: inherit`.

## Verification

No automated tests for this change (user decision). Checks:
- `npm run build` passes (`vue-tsc` type-check).
- By hand in the browser, at 100% and 125% zoom: the list sits right under the field, at the same width; typing filters; arrows, Enter, Escape and mouse picking work; the list reloads on focus.

## Out of scope

- Tailwind or any UI library: one component doesn't justify a new styling system.
- Virtualising long lists: the Source stub seeds 20 documents.
- Restyling other components.
- Automated tests and a frontend test runner (user decision).
