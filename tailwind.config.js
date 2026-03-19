/** @type {import('tailwindcss').Config} */
module.exports = {
  presets: [require('./src/pr_guardian/dashboard/vendor/preset')],
  content: ['./src/pr_guardian/dashboard/*.html', './src/pr_guardian/dashboard/static/*.js'],
  darkMode: 'class',
  safelist: [
    // Dynamically constructed in JS template literals
    'sev-critical', 'sev-high', 'sev-medium', 'sev-low',
    'verdict-pass', 'verdict-warn', 'verdict-flag_human',
    'banner-auto_approve', 'banner-human_review', 'banner-hard_block', 'banner-pending', 'banner-error',
    'badge-auto_approve', 'badge-human_review', 'badge-hard_block', 'badge-pending', 'badge-error',
    'badge-trivial', 'badge-low', 'badge-medium', 'badge-high',
    'log-badge-info', 'log-badge-warn', 'log-badge-error', 'log-badge-debug',
    'stage-discovery', 'stage-mechanical', 'stage-triage', 'stage-agents', 'stage-decision', 'stage-complete', 'stage-queued', 'stage-error',
    'type-recent_changes', 'type-maintenance',
    'scan-stage-complete', 'scan-stage-error', 'scan-stage-discovery', 'scan-stage-scan_discovery', 'scan-stage-scan_analysis', 'scan-stage-scan_sampling', 'scan-stage-scan_report',
    'effort-small', 'effort-medium', 'effort-large',
    // Area card left-border accents
    'border-l-[3px]',
    'border-l-emerald-400/50', 'border-l-orange-400/50', 'border-l-red-400/50',
    'bg-orange-400/[0.02]', 'bg-red-400/[0.03]',
    // Dismiss button
    'border-amber-400/40', 'bg-amber-400/10', 'text-amber-400', 'hover:bg-amber-400/20',
    // Finding card severity borders
    'border-l-red-400', 'border-l-orange-400', 'border-l-yellow-400', 'border-l-slate-600',
  ],
};
