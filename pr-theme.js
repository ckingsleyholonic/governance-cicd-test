(() => {
  const button = document.getElementById('report-theme');
  const system = matchMedia('(prefers-color-scheme: dark)');
  const key = 'holonic-pr-theme';
  let choice = new URL(location.href).searchParams.get('theme');
  try {
    choice = choice || localStorage.getItem(key);
  } catch {
    // Sandboxed public reports keep the preference in the report URL.
  }

  function update() {
    const explicit = choice === 'light' || choice === 'dark';
    const dark = explicit ? choice === 'dark' : system.matches;
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
    button.textContent = dark ? '☀' : '☾';
    button.title = dark ? 'Use light theme' : 'Use dark theme';
    button.setAttribute('aria-label', button.title);
    button.hidden = false;
  }

  button.addEventListener('click', () => {
    choice = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    update();
    try {
      localStorage.setItem(key, choice);
    } catch {
      const url = new URL(location.href);
      url.searchParams.set('theme', choice);
      history.replaceState(history.state, '', url);
    }
  });
  system.addEventListener('change', update);
  update();
})();
