import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Chapters, Config, Lesson } from '../api';
import { Home } from './Home';

const course: Config = {
  title: 'How LLMs work',
  description: 'A short course on how large language models work.',
  language: 'en',
  registration_open: false,
};

function lesson(id: string, position: number, overrides: Partial<Lesson> = {}): Lesson {
  return {
    id, title: id.replace(/-/g, ' '), position, status: 'available', is_in_progress: false,
    has_open_work: true, cards_total: 4, cards_ready: 0, ...overrides,
  };
}

function board(overrides: Partial<Chapters> = {}): Chapters {
  return {
    day: '2026-10-07',
    cards_total: 12,
    cards_ready: 5,
    next_lesson: { id: 'training-data', title: 'training data', chapter_id: 'training', action: 'start' },
    review_due: 3,
    review_sessions_remaining: 1,
    review_session_size: 10,
    practice_available: true,
    practice_card_count: 5,
    program_version: 'abc123def456',
    chapters: [
      {
        id: 'foundations', title: 'Foundations', position: 1, lessons_total: 2, lessons_completed: 2,
        cards_total: 8, cards_ready: 5,
        lessons: [
          lesson('tokens', 1, { status: 'completed', has_open_work: false, cards_ready: 4 }),
          lesson('embeddings', 2, { status: 'completed', has_open_work: true, cards_ready: 1 }),
        ],
      },
      {
        id: 'training', title: 'Training', position: 2, lessons_total: 1, lessons_completed: 0,
        cards_total: 4, cards_ready: 0,
        lessons: [lesson('training-data', 1)],
      },
    ],
    ...overrides,
  };
}

afterEach(cleanup);

describe('Home', () => {
  it('shows the course and how many cards are ready', () => {
    render(<Home course={course} chapters={board()} onStart={vi.fn()} />);
    expect(screen.getByRole('heading', { level: 1, name: 'How LLMs work' })).toBeTruthy();
    expect(screen.getByText('5 of 12 cards ready')).toBeTruthy();
  });

  it('highlights the next lesson and starts it', () => {
    const onStart = vi.fn();
    render(<Home course={course} chapters={board()} onStart={onStart} />);

    const next = screen.getByRole('region', { name: 'Next lesson' });
    expect(within(next).getByText('training data')).toBeTruthy();
    fireEvent.click(within(next).getByRole('button', { name: 'Start lesson' }));
    expect(onStart).toHaveBeenCalledWith({ type: 'lesson', id: 'training-data' });

    const row = screen.getByRole('button', { name: /training data/ });
    expect(row.getAttribute('aria-current')).toBe('step');
    expect(within(row).getByText('Next')).toBeTruthy();
    expect(screen.getAllByText('Next')).toHaveLength(1);
  });

  it('offers to continue a lesson in progress', () => {
    const chapters = board({ next_lesson: { id: 'training-data', title: 'training data', chapter_id: 'training', action: 'continue' } });
    render(<Home course={course} chapters={chapters} onStart={vi.fn()} />);
    const next = screen.getByRole('region', { name: 'Continue lesson' });
    expect(within(next).getByRole('button', { name: 'Continue lesson' })).toBeTruthy();
  });

  it('flags a finished lesson that has open work and reopens it as a lesson', () => {
    const onStart = vi.fn();
    render(<Home course={course} chapters={board()} onStart={onStart} />);

    const updated = screen.getByRole('button', { name: /embeddings/ });
    expect(within(updated).getByText('New cards')).toBeTruthy();
    fireEvent.click(updated);
    expect(onStart).toHaveBeenLastCalledWith({ type: 'lesson', id: 'embeddings' });

    const finished = screen.getByRole('button', { name: /tokens/ });
    expect(within(finished).queryByText('New cards')).toBeNull();
    fireEvent.click(finished);
    expect(onStart).toHaveBeenLastCalledWith({ type: 'lesson-practice', id: 'tokens' });
  });

  it('pluralises the review count', () => {
    const { rerender } = render(<Home course={course} chapters={board({ review_due: 3 })} onStart={vi.fn()} />);
    expect(screen.getByRole('button', { name: /Review 3 cards/ })).toBeTruthy();

    rerender(<Home course={course} chapters={board({ review_due: 1 })} onStart={vi.fn()} />);
    expect(screen.getByRole('button', { name: /Review 1 card(?!s)/ })).toBeTruthy();
  });

  it('starts review and practice, and disables them when there is nothing to do', () => {
    const onStart = vi.fn();
    const { rerender } = render(<Home course={course} chapters={board()} onStart={onStart} />);
    fireEvent.click(screen.getByRole('button', { name: /Review 3 cards/ }));
    expect(onStart).toHaveBeenLastCalledWith({ type: 'review' });
    const practice = screen.getByRole('button', { name: /Practice/ });
    expect(practice.textContent).toContain('5 cards');
    fireEvent.click(practice);
    expect(onStart).toHaveBeenLastCalledWith({ type: 'practice' });

    rerender(<Home course={course} chapters={board({ review_due: 0, practice_available: false, practice_card_count: 0 })} onStart={onStart} />);
    expect((screen.getByRole('button', { name: /Review/ }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: /Practice/ }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('says so when every lesson is done', () => {
    render(<Home course={course} chapters={board({ next_lesson: null })} onStart={vi.fn()} />);
    expect(screen.queryByRole('region', { name: 'Next lesson' })).toBeNull();
    expect(screen.getByText('All lessons complete')).toBeTruthy();
  });
});
