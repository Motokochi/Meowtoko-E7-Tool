import type { Configuration } from 'webpack';
import ForkTsCheckerWebpackPlugin from 'fork-ts-checker-webpack-plugin';

import { rules } from './webpack.rules';

export const rendererConfig: Configuration = {
  // Forge's default development source maps use eval(), which the renderer CSP
  // intentionally forbids. Emit normal source maps so development and release
  // windows exercise the same no-unsafe-eval policy.
  devtool: 'source-map',
  module: {
    rules: [
      ...rules,
      {
        test: /\.css$/,
        use: ['style-loader', 'css-loader'],
      },
    ],
  },
  plugins: [new ForkTsCheckerWebpackPlugin()],
  resolve: {
    extensions: ['.js', '.ts', '.jsx', '.tsx', '.css', '.json'],
  },
};
