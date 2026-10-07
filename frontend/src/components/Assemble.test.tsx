import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Step } from '../api';
import { Assemble } from './Assemble';

const step: Step = {
  id: 'p1:assemble:short:1a2b3c4d:0',
  kind: 'assemble',
  card_id: 'short',
  prompt: 'What is a token?',
  answer: 'A word piece.',
  hint: null,
  note: null,
  tiles: ['piece.', 'A', 'word'],
};

const pool = () => within(screen.getByRole('group', { name: 'Words' }));
const focused = () => document.activeElement?.textContent;

afterEach(cleanup);

describe('Assemble', () => {
  it('keeps focus in the word pool as words are picked, then moves it to Check', () => {
    const onAnswer = vi.fn();
    render(<Assemble step={step} disabled={false} onAnswer={onAnswer} />);
    expect(screen.getByText('Pick the words in order')).toBeTruthy();

    // "A" sits in the middle; the word that slides into its place gets focus.
    const a = pool().getByRole('button', { name: 'A' });
    a.focus();
    fireEvent.click(a);
    expect(focused()).toBe('word');

    // The last word in the pool: focus falls back to the one before it.
    fireEvent.click(pool().getByRole('button', { name: 'word' }));
    expect(focused()).toBe('piece.');

    fireEvent.click(pool().getByRole('button', { name: 'piece.' }));
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Check' }));
    fireEvent.click(document.activeElement!);
    expect(onAnswer).toHaveBeenCalledWith('A word piece.');
  });

  it('moves focus to the word that went back when one is removed with Backspace', () => {
    render(<Assemble step={step} disabled={false} onAnswer={vi.fn()} />);
    fireEvent.keyDown(document.body, { key: '2' }); // "A"
    fireEvent.keyDown(document.body, { key: '2' }); // "word"
    expect(screen.getByText(/A word$/)).toBeTruthy();
    fireEvent.keyDown(document.body, { key: 'Backspace' });
    expect(focused()).toBe('word');
    expect(document.activeElement?.closest('[role="group"]')).toBe(screen.getByRole('group', { name: 'Words' }));
  });

  it('keeps focus on "Remove last word" while there is more to remove', () => {
    render(<Assemble step={step} disabled={false} onAnswer={vi.fn()} />);
    fireEvent.click(pool().getByRole('button', { name: 'A' }));
    fireEvent.click(pool().getByRole('button', { name: 'word' }));
    const remove = screen.getByRole('button', { name: 'Remove last word' });
    remove.focus();
    fireEvent.click(remove);
    expect(document.activeElement).toBe(remove);
    // The last placed word: the button turns disabled, so focus follows the word back.
    fireEvent.click(remove);
    expect(focused()).toBe('A');
  });
});
