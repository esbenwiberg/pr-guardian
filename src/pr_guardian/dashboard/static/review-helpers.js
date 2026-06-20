// Shared client-side helpers for review-mode templates (chapter view + wizard).
// Mirrors discovery/file_roles.py for the role classifier.

window.PRG = window.PRG || {};

window.PRG.SEV_ORDER = { critical: 0, high: 1, medium: 2, low: 3 };

window.PRG.detectRole = function detectRole(path) {
  const p = path;
  const pl = p.toLowerCase();
  if (/\/(tests?|spec|__tests?|__specs?)\//i.test(p) ||
      /\.(test|spec)\.[a-z]+$/i.test(p) ||
      /_(test|spec)\.[a-z]+$/i.test(p)) return 'TEST';
  if (/dockerfile/i.test(pl) || /docker-compose/i.test(pl) ||
      /\.github\//i.test(pl) || /\/(ci|circleci|github)\//i.test(pl) ||
      /jenkinsfile/i.test(pl) || /\.travis\.yml/i.test(pl)) return 'INFRA';
  if (/\/(docs?|documentation|wiki)\//i.test(pl) ||
      /^readme/i.test(pl.split('/').pop()) ||
      /\.(md|rst|txt)$/i.test(pl)) return 'DOCS';
  if (/(package-lock\.json|yarn\.lock|poetry\.lock|cargo\.lock|composer\.lock|gemfile\.lock)/i.test(pl)) return 'GENERATED';
  if (/(requirements.*\.txt|package\.json|pyproject\.toml|go\.mod|cargo\.toml|pom\.xml|gemfile|composer\.json)$/i.test(pl)) return 'DEPENDENCY';
  if (/(makefile|build\.gradle|cmakefile|setup\.py|setup\.cfg|\.bazel|buck|\.mk)$/i.test(pl)) return 'BUILD';
  if (/\.(env|config|cfg|ini|yaml|yml|toml|json|conf)$/i.test(pl) &&
      !/package\.json$|pyproject\.toml$/i.test(pl)) return 'CONFIG';
  return 'PRODUCTION';
};

// Canonical duration formatter. Every dashboard surface renders elapsed time
// through this so one review never shows "126.5s" here and "2m 6s" there.
window.PRG.formatDuration = function formatDuration(ms) {
  if (!ms) return '-';
  if (ms < 1000) return `${ms}ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  const totalSec = Math.floor(s);
  const m = Math.floor(totalSec / 60);
  if (m < 60) return `${m}m ${totalSec % 60}s`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ${m % 60}m`;
  const d = Math.floor(h / 24);
  return `${d}d ${h % 24}h`;
};

window.PRG.escapeHtml = function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
};
