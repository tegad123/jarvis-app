// Tailwind config for the preview build only. The app uses tailwind/preset.cjs.
const path = require('path')
const root = path.join(__dirname, '..')

module.exports = {
  presets: [require('../tailwind/preset.cjs')],
  content: [
    path.join(root, 'components/**/*.{ts,tsx,html}'),
    path.join(root, 'previews/**/*.{html,js}'),
  ],
}
