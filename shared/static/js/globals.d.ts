// What these classic scripts find on the page when they run: globals the templates
// and vendored libraries set, which TypeScript cannot see from the .js files alone.
// Read only by `// @ts-check` (jsconfig.json); nothing here is served.

/** The strings table settings.html sets in a data island just before settings.js. */
declare const T: Record<string, string>;

/** Vendored under shared/static/js, loaded by the templates that use them. */
declare const htmx: any;
declare const bootstrap: any;
/** xterm.js, fetched by install.sh for the terminal tab. */
declare const Terminal: any;

/** Set by panel.js, called from templates and settings.js. */
declare function panelShowTab(target: string): void;

interface Window {
    bootstrap: any;
    htmx: any;
    Terminal: any;
    panelShowTab: typeof panelShowTab;
    copyKey: (btn: HTMLElement, text: string) => void;
    /** The event-name vocabulary the page renders into a data island for ws.js. */
    EVENT_VOCAB: Record<string, string>;
}

/** Global Privacy Control (count.js, docs/app.md `C-10`): not yet in TypeScript's DOM
 *  types, and absent from browsers that do not send it. */
interface Navigator {
    globalPrivacyControl?: boolean;
}

// getElementById returns a plain HTMLElement, and the scripts read form-control
// properties off it throughout. Declaring the four they use is the alternative to
// a cast at every one of ~80 call sites; a typo in the name is still caught.
interface HTMLElement {
    disabled: boolean;
    value: string;
    checked: boolean;
    submit(): void;
}

// Likewise for queries: these scripts only ever select HTML elements, never SVG or
// MathML, so a query result is typed as the HTMLElement it always is.
interface ParentNode {
    querySelector(selectors: string): HTMLElement | null;
    querySelectorAll(selectors: string): NodeListOf<HTMLElement>;
}
interface Element {
    closest(selectors: string): HTMLElement | null;
}
