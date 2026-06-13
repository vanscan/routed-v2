module.exports = {
  preset: 'ts-jest',
  testEnvironment: 'node',
  testMatch: ['**/__tests__/**/*.test.ts', '**/__tests__/**/*.test.js'],
  moduleFileExtensions: ['ts', 'tsx', 'js', 'jsx'],
  transform: {
    '^.+\\.tsx?$': ['ts-jest', { tsconfig: { strict: false } }],
  },
  // Don't try to transform node_modules
  transformIgnorePatterns: ['/node_modules/'],
  // Mock AsyncStorage for syncQueue tests
  moduleNameMapper: {
    '@react-native-async-storage/async-storage': '<rootDir>/src/__mocks__/asyncStorage.ts',
  },
};
