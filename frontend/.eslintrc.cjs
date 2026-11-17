module.exports = {
  root: true,
  env: { browser: true, es2020: true },
  extends: [
    'eslint:recommended',
    'plugin:@typescript-eslint/recommended',
    'plugin:react-hooks/recommended',
  ],
  parser: '@typescript-eslint/parser',
  parserOptions: { ecmaVersion: 'latest', sourceType: 'module' },
  plugins: ['@typescript-eslint', 'react-hooks', 'i18next'],
  ignorePatterns: ['dist', 'node_modules', '.eslintrc.cjs'],
  rules: {
    // Contrôle CI : aucune chaîne visible écrite en dur dans un composant.
    // Tout libellé affiché passe par i18n (t()).
    'i18next/no-literal-string': ['error', { mode: 'jsx-text-only' }],
  },
  overrides: [
    {
      // Les tests comparent des libellés traduits en clair : la règle ne s'y
      // applique pas (ce ne sont pas des composants de l'interface).
      files: ['**/*.test.ts', '**/*.test.tsx', 'src/test-setup.ts'],
      rules: { 'i18next/no-literal-string': 'off' },
    },
  ],
}
