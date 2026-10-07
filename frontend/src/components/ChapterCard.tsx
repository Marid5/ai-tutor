import type { Chapter, Lesson, SessionTarget } from '../api';
import { count } from '../plural';
import { Icon } from './Icon';
import { ProgressBar } from './ProgressBar';

/**
 * A finished lesson with nothing new replays as practice; any other lesson,
 * including a finished one whose cards were added or changed, opens as a lesson.
 */
export function lessonTarget(lesson: Lesson): SessionTarget {
  return lesson.status === 'completed' && !lesson.has_open_work
    ? { type: 'lesson-practice', id: lesson.id }
    : { type: 'lesson', id: lesson.id };
}

type Badge = { text: string; tone: 'accent' | 'warn' | 'good' };

function lessonBadge(lesson: Lesson, isNext: boolean): Badge | null {
  if (lesson.is_in_progress) return { text: 'In progress', tone: 'accent' };
  if (isNext) return { text: 'Next', tone: 'accent' };
  if (lesson.status === 'completed') {
    return lesson.has_open_work ? { text: 'New cards', tone: 'warn' } : { text: 'Done', tone: 'good' };
  }
  return null;
}

function lessonMeta(lesson: Lesson): string {
  const cards = count(lesson.cards_total, 'card');
  return lesson.cards_ready > 0 ? `${cards} · ${lesson.cards_ready} ready` : cards;
}

interface ChapterCardProps {
  chapter: Chapter;
  nextLessonId: string | null;
  onStart: (target: SessionTarget) => void;
}

export function ChapterCard({ chapter, nextLessonId, onStart }: ChapterCardProps) {
  const headingId = `chapter-${chapter.id}`;
  return (
    <section className="card chapter" aria-labelledby={headingId}>
      <header className="chapter-head">
        <div>
          <p className="eyebrow">Chapter {chapter.position}</p>
          <h3 id={headingId}>{chapter.title}</h3>
        </div>
        <span className="chip">{chapter.cards_ready}/{chapter.cards_total} ready</span>
      </header>
      <ProgressBar value={chapter.cards_ready} max={chapter.cards_total} size="sm"
        tone={chapter.cards_total > 0 && chapter.cards_ready === chapter.cards_total ? 'good' : 'accent'}
        label={`${chapter.title}: ${chapter.cards_ready} of ${count(chapter.cards_total, 'card')} ready`} />
      <ol className="lessons">
        {chapter.lessons.map(lesson => {
          const isNext = lesson.id === nextLessonId;
          const badge = lessonBadge(lesson, isNext);
          const done = lesson.status === 'completed';
          return (
            <li key={lesson.id}>
              <button type="button" className={`lesson${isNext ? ' is-next' : ''}`}
                aria-current={isNext ? 'step' : undefined}
                onClick={() => onStart(lessonTarget(lesson))}>
                <span className={`lesson-index${done ? ' is-done' : ''}`}>
                  {done ? <Icon name="check" /> : lesson.position}
                </span>
                <span className="lesson-text">
                  <span className="lesson-title">{lesson.title}</span>{' '}
                  <span className="lesson-meta">{lessonMeta(lesson)}</span>
                </span>
                {badge && <>{' '}<span className={`badge badge-${badge.tone}`}>{badge.text}</span></>}
              </button>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
