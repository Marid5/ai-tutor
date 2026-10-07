import { describe, expect, it } from 'vitest';
import { count, plural } from './plural';

describe('plural', () => {
  it('uses the singular only for exactly one', () => {
    expect(plural(1, 'card')).toBe('card');
    expect(plural(0, 'card')).toBe('cards');
    expect(plural(2, 'card')).toBe('cards');
    expect(plural(21, 'card')).toBe('cards');
  });

  it('accepts an irregular plural', () => {
    expect(plural(1, 'lesson is', 'lessons are')).toBe('lesson is');
    expect(plural(3, 'lesson is', 'lessons are')).toBe('lessons are');
  });
});

describe('count', () => {
  it('prefixes the number', () => {
    expect(count(1, 'card')).toBe('1 card');
    expect(count(0, 'card')).toBe('0 cards');
    expect(count(12, 'minute')).toBe('12 minutes');
  });

  it('groups thousands for readability', () => {
    expect(count(1200, 'check')).toBe('1,200 checks');
  });
});
