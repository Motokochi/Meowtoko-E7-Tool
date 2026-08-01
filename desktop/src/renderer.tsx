import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import { App } from './app';
import { ThemeProvider } from './theme';
import './styles.css';

const root = document.getElementById('root');

if (!root) {
  throw new Error('The renderer root element was not found.');
}

createRoot(root).render(
  <StrictMode>
    <ThemeProvider>
      <App />
    </ThemeProvider>
  </StrictMode>,
);
