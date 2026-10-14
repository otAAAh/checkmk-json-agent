// Copyright (C) 2026 Benjamin Knapp
// SPDX-License-Identifier: GPL-2.0-only
//
// Checkmk's own ESLint setup, so the wizard follows the rules of the code it is
// built from: the base list is Checkmk's root eslint.config.mjs, the rules are
// checkmkVueConfig() and checkmkVueModuleScopeTranslationConfig() from
// packages/cmk-ui-library/eslint.shared.mjs. Copied rather than imported, like
// .stylelintrc.mjs: CI lints a plain clone without a Checkmk checkout. When
// Checkmk changes those rules, update this copy.
//
// Type-aware rules (no-floating-promises, ...) use tsconfig.json. Its `@/` paths
// need the frontend/.cmk link (see there); without it the cmk-frontend-vue
// imports have no types, so those rules see less but still run.
import js from '@eslint/js'
import skipFormatting from '@vue/eslint-config-prettier/skip-formatting'
import vueTsEslintConfig from '@vue/eslint-config-typescript'
import pluginVue from 'eslint-plugin-vue'
import tseslint from 'typescript-eslint'

const NO_RANDOM_UUID = {
  selector: "MemberExpression[property.name='randomUUID']",
  message:
    "crypto.randomUUID() is not available in all environments. Use randomId from 'cmk-ui-library/lib/randomId' instead.",
}

const NO_MODULE_SCOPE_TRANSLATION = {
  selector: 'CallExpression[callee.name=/^_t(n|p|np)?$/]:not(:function *)',
  message:
    'Translated text minted at module scope resolves before the translation catalog is ' +
    'loaded and freezes to English. Move the call into a function or composable so it ' +
    'runs once the app is mounted.',
}

export default [
  { name: 'app/files-to-lint', files: ['src/**/*.{ts,vue}'] },
  { name: 'app/files-to-ignore', ignores: ['dist/**', 'node_modules/**', '.cmk/**'] },

  js.configs.recommended,
  ...pluginVue.configs['flat/recommended'],
  ...tseslint.configs.recommended,
  ...vueTsEslintConfig(),
  skipFormatting,

  {
    name: 'checkmk/vue',
    files: ['src/**/*.{ts,vue}'],
    languageOptions: {
      parserOptions: {
        project: ['tsconfig.json'],
        tsconfigRootDir: import.meta.dirname,
        parser: '@typescript-eslint/parser',
        ecmaVersion: 'latest',
      },
    },
    rules: {
      '@typescript-eslint/consistent-type-imports': 'error',
      '@typescript-eslint/no-misused-promises': 'error',
      '@typescript-eslint/no-floating-promises': 'error',
      '@typescript-eslint/naming-convention': [
        'error',
        { selector: 'import', format: ['camelCase', 'PascalCase'] },
        { selector: 'variableLike', format: ['camelCase', 'UPPER_CASE'], leadingUnderscore: 'allow' },
        { selector: 'typeLike', format: ['PascalCase'] },
        { selector: 'property', format: [] },
      ],
      'no-unused-vars': 'off',
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
      eqeqeq: 'error',
      'vue/eqeqeq': 'error',
      'no-var': 'error',
      'no-restricted-syntax': ['error', NO_RANDOM_UUID],
      curly: 'error',
      'prefer-template': 'error',
      'vue/prefer-template': 'error',
      'vue/prop-name-casing': 'off',
      'vue/require-default-prop': 'off',
      'vue/no-import-compiler-macros': 'error',
      'vue/no-undef-components': 'error',
      'vue/no-bare-strings-in-template': [
        'error',
        {
          // Checkmk's list, plus the two status marks the connection step shows
          // in front of a translated message.
          allowlist: [
            'x', '(', ')', ',', '.', '&', '+', '-', '=', '*', '/', '#', '%', '!', '?', ':',
            '[', ']', '{', '}', '<', '>', '·', '•', '‐', '–', '—',
            '−', '|', '✓', '✗',
          ],
          attributes: {
            '/.+/': ['title', 'aria-label', 'aria-placeholder', 'aria-roledescription', 'aria-valuetext'],
            input: ['placeholder'],
            img: ['alt'],
          },
          directives: ['v-text'],
        },
      ],
    },
  },

  {
    name: 'checkmk/module-scope-translation',
    files: ['src/**/*.ts'],
    rules: {
      'no-restricted-syntax': ['error', NO_RANDOM_UUID, NO_MODULE_SCOPE_TRANSLATION],
    },
  },
]
