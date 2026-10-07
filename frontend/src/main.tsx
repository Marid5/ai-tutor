import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import './styles.css';
import { applyTheme, readTheme } from './theme';

// Apply the saved theme before the first render so the page never flashes in
// the wrong colours. This runs from the bundle: the CSP allows no inline script.
applyTheme(readTheme());

const root = document.getElementById('root');
if (!root) throw new Error('index.html has no #root element');
createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
