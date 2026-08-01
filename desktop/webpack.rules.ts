import type { RuleSetRule } from 'webpack';

export const rules: RuleSetRule[] = [
  {
    test: /\.tsx?$/,
    exclude: /(node_modules|\.webpack)/,
    use: {
      loader: 'ts-loader',
      options: {
        transpileOnly: true,
      },
    },
  },
  {
    test: /assets[\\/]equipment[\\/]slots[\\/].+\.png$/i,
    type: 'asset/resource',
    generator: {
      filename: 'assets/equipment/slots/[name][ext]',
    },
  },
  {
    test: /assets[\\/]equipment[\\/]sets[\\/].+\.png$/i,
    type: 'asset/resource',
    generator: {
      filename: 'assets/equipment/sets/[name][ext]',
    },
  },
];
