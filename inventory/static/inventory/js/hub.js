(() => {
  const root = document.documentElement;
  const button = document.querySelector('[data-hub-theme-toggle]');
  if (!button) return;

  const applyTheme = (theme) => {
    const isLight = theme === 'light';
    root.dataset.theme = theme;
    button.setAttribute('aria-pressed', String(!isLight));
    document.querySelector('meta[name="theme-color"]')?.setAttribute(
      'content',
      isLight ? '#ffffff' : '#191d20'
    );
  };

  let initialTheme = 'light';
  try {
    initialTheme = localStorage.getItem('bbx-theme')
      || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  } catch (_) {
    initialTheme = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  applyTheme(initialTheme);

  button.addEventListener('click', () => {
    const nextTheme = root.dataset.theme === 'dark' ? 'light' : 'dark';
    try { localStorage.setItem('bbx-theme', nextTheme); } catch (_) { /* Storage may be unavailable. */ }
    applyTheme(nextTheme);
  });
})();
