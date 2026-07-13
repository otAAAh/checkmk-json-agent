// Adopted from Checkmk's repo-root .stylelintrc.mjs (same rules + the
// checkmk/vue-bem-naming-convention plugin, vendored under scripts/ with one
// extra path→prefix mapping "je" for src/explorer/). Kept in sync with cmk so
// our Vue styling follows the same conventions as the components we build on.
/** @type {import('stylelint').Config} */
export default {
  extends: 'stylelint-config-standard',
  rules: {
    'selector-class-pattern': null
  },
  plugins: ['./scripts/stylelint-vue-bem-naming-convention.js'],
  overrides: [
    {
      files: ['*.css', '**/*.css'],
      rules: {
        'selector-class-pattern': [
          '^$',
          {
            message: 'Expected no selectors in css files, only variable definitions.'
          }
        ]
      }
    },
    {
      files: ['*.vue', '**/*.vue'],
      customSyntax: 'postcss-html',
      extends: ['stylelint-config-standard'],
      rules: {
        'selector-nested-pattern': [
          '^(&(\\.|:|#|\\[|\\s|>|\\+|~)|[^&])',
          {
            message:
              'Expected "%s" to match CSS nesting pattern. Only native CSS nesting allowed.'
          }
        ],
        'keyframes-name-pattern': ['^([a-z][a-z0-9]*)((-|_|--|__)[a-z0-9]+)*$'],
        'declaration-property-value-no-unknown': null,
        'selector-pseudo-class-no-unknown': [
          true,
          { ignorePseudoClasses: ['slotted', 'deep', 'global'] }
        ],
        'value-keyword-case': ['lower', { ignoreFunctions: ['v-bind'] }],
        'checkmk/vue-bem-naming-convention': true,
        'no-empty-source': [true, { message: 'No empty <style> section allowed in vue files.' }]
      }
    }
  ]
}
