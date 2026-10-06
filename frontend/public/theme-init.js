// 在样式加载前应用已保存的选择，避免页面先闪现另一种主题。
(() => {
  const home = /\/(?:index|home|intro)?(?:\.html)?$/.test(location.pathname);
  let value = home ? 'light' : 'dark';
  try {
    const saved = localStorage.getItem('huimai-theme');
    if (saved === 'light' || saved === 'dark') value = saved;
  } catch { /* 存储不可用时仍可在当前页面切换。 */ }
  document.documentElement.dataset.theme = value;
})();
