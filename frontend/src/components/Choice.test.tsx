import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Choice } from './Choice';

const OPTIONS = ['a whole sentence', 'a word or word piece', 'a full paragraph', 'a line of code'];

const option = (index: number) => screen.getAllByRole('button')[index] as HTMLButtonElement;

afterEach(cleanup);

describe('Choice', () => {
  it('numbers the options so they can be picked with 1-4', () => {
    render(<Choice options={OPTIONS} disabled={false} onAnswer={vi.fn()} />);
    const buttons = screen.getAllByRole('button');
    expect(buttons).toHaveLength(4);
    expect(buttons[1].textContent).toBe('2a word or word piece');
    expect(buttons[1].getAttribute('aria-keyshortcuts')).toBe('2');
  });

  it('selects an option with a digit key and submits it with Enter', () => {
    const onAnswer = vi.fn();
    render(<Choice options={OPTIONS} disabled={false} onAnswer={onAnswer} />);

    fireEvent.keyDown(document.body, { key: '2' });
    expect(document.activeElement).toBe(option(1));
    expect(option(1).classList.contains('is-selected')).toBe(true);
    expect(onAnswer).not.toHaveBeenCalled();

    fireEvent.keyDown(document.body, { key: '4' });
    expect(document.activeElement).toBe(option(3));
    expect(option(1).classList.contains('is-selected')).toBe(false);

    const enter = fireEvent.keyDown(document.activeElement!, { key: 'Enter' });
    expect(enter).toBe(false); // default prevented: the focused button must not answer a second time
    expect(onAnswer).toHaveBeenCalledTimes(1);
    expect(onAnswer).toHaveBeenCalledWith('a line of code');
  });

  it('ignores Enter before anything is selected, digits out of range and shortcuts with modifiers', () => {
    const onAnswer = vi.fn();
    render(<Choice options={OPTIONS} disabled={false} onAnswer={onAnswer} />);
    fireEvent.keyDown(document.body, { key: 'Enter' });
    fireEvent.keyDown(document.body, { key: '5' });
    fireEvent.keyDown(document.body, { key: '0' });
    fireEvent.keyDown(document.body, { key: '1', ctrlKey: true });
    fireEvent.keyDown(document.body, { key: '1', metaKey: true });
    fireEvent.keyDown(document.body, { key: 'Enter' });
    expect(onAnswer).not.toHaveBeenCalled();
    expect(screen.getAllByRole('button').some(button => button.classList.contains('is-selected'))).toBe(false);
  });

  it('answers on a click or tap', () => {
    const onAnswer = vi.fn();
    render(<Choice options={OPTIONS} disabled={false} onAnswer={onAnswer} />);
    fireEvent.click(option(2));
    expect(onAnswer).toHaveBeenCalledWith('a full paragraph');
  });

  it('takes no input while locked and marks the chosen option with the server verdict', () => {
    const onAnswer = vi.fn();
    render(<Choice options={OPTIONS} disabled onAnswer={onAnswer} chosen="a full paragraph" verdict={false} />);
    fireEvent.keyDown(document.body, { key: '1' });
    fireEvent.keyDown(document.body, { key: 'Enter' });
    fireEvent.click(option(0));
    expect(onAnswer).not.toHaveBeenCalled();
    expect(option(2).dataset.state).toBe('wrong');
    expect(option(0).dataset.state).toBeUndefined();
    // Not by colour alone: the verdict is in the button's name too.
    expect(screen.getByRole('button', { name: 'a full paragraph, incorrect' })).toBe(option(2));
  });

  it('names the chosen option correct when the server says so', () => {
    render(<Choice options={OPTIONS} disabled onAnswer={vi.fn()} chosen="a word or word piece" verdict />);
    expect(screen.getByRole('button', { name: 'a word or word piece, correct' }).dataset.state).toBe('right');
  });

  it('shows no verdict on the chosen option while the server is still checking it', () => {
    render(<Choice options={OPTIONS} disabled onAnswer={vi.fn()} chosen="a word or word piece" verdict={null} />);
    expect(option(1).dataset.state).toBe('chosen');
    expect(screen.getByRole('button', { name: 'a word or word piece, your answer' })).toBe(option(1));
  });
});
