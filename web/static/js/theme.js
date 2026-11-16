// Applies the admin-selected theme to the running page (no reload needed).
const VARS = {
  bg_dark: '--bg', bg_card: '--card', bg_card_selected: '--card-sel', border: '--border',
  primary: '--primary', primary_hover: '--primary-h', success: '--success', success_hover: '--success-h',
  danger: '--danger', danger_hover: '--danger-h', text: '--text', text_muted: '--muted',
};

export function applyTheme(colors) {
  const root = document.documentElement.style;
  for (const [key, cssVar] of Object.entries(VARS)) if (colors[key]) root.setProperty(cssVar, colors[key]);
}
