import type { Chapters, Config, SessionTarget } from '../api';
import { ChapterCard } from '../components/ChapterCard';
import { Icon } from '../components/Icon';
import { ProgressBar } from '../components/ProgressBar';
import { count } from '../plural';

interface HomeProps {
  course: Config | null;
  chapters: Chapters;
  onStart: (target: SessionTarget) => void;
  /** A message from the last action, e.g. a session that could not start. */
  notice?: string;
  /** Disables the start buttons while a session is being opened. */
  busy?: boolean;
}

export function Home({ course, chapters, onStart, notice, busy = false }: HomeProps) {
  const next = chapters.next_lesson;
  const nextChapter = next ? chapters.chapters.find(chapter => chapter.id === next.chapter_id) : undefined;
  const nextLesson = nextChapter?.lessons.find(lesson => lesson.id === next?.id);
  const nextLabel = next?.action === 'continue' ? 'Continue lesson' : 'Next lesson';
  const percent = chapters.cards_total ? Math.round((chapters.cards_ready / chapters.cards_total) * 100) : 0;
  const reviewDue = chapters.review_due;

  return (
    <div className="home">
      <section className="hero">
        <h1 tabIndex={-1}>{course?.title || 'Your course'}</h1>
        {course?.description && <p className="lede">{course.description}</p>}
        <div className="readiness">
          <div className="readiness-line">
            <span>{chapters.cards_ready} of {count(chapters.cards_total, 'card')} ready</span>
            <span className="readiness-percent">{percent}%</span>
          </div>
          <ProgressBar value={chapters.cards_ready} max={chapters.cards_total} size="lg" label="Cards ready" />
        </div>
      </section>

      {notice && <p className="notice" role="status">{notice}</p>}

      {next ? (
        <section className="card next-lesson" aria-labelledby="next-lesson-label">
          <p className="eyebrow eyebrow-accent" id="next-lesson-label">{nextLabel}</p>
          <h2>{next.title}</h2>
          {nextChapter && (
            <p className="muted">
              Chapter {nextChapter.position} · {nextChapter.title}
              {nextLesson ? ` · ${count(nextLesson.cards_total, 'card')}` : ''}
            </p>
          )}
          <button type="button" className="button primary" disabled={busy}
            onClick={() => onStart({ type: 'lesson', id: next.id })}>
            <span>{next.action === 'continue' ? 'Continue lesson' : 'Start lesson'}</span>
            <Icon name="arrow" />
          </button>
        </section>
      ) : (
        <section className="card all-done">
          <span className="all-done-mark" aria-hidden="true"><Icon name="check" /></span>
          <div>
            <h2>All lessons complete</h2>
            <p className="muted">Keep what you learned fresh with review and practice.</p>
          </div>
        </section>
      )}

      <div className="actions">
        <button type="button" className="tile" disabled={busy || reviewDue === 0}
          onClick={() => onStart({ type: 'review' })}>
          <span className="tile-icon" aria-hidden="true"><Icon name="review" /></span>
          <span className="tile-title">{reviewDue > 0 ? `Review ${count(reviewDue, 'card')}` : 'Review'}</span>{' '}
          <span className="tile-sub">{reviewDue > 0 ? 'Due today' : 'Nothing due today'}</span>
        </button>
        <button type="button" className="tile" disabled={busy || !chapters.practice_available}
          onClick={() => onStart({ type: 'practice' })}>
          <span className="tile-icon" aria-hidden="true"><Icon name="practice" /></span>
          <span className="tile-title">Practice</span>{' '}
          <span className="tile-sub">
            {chapters.practice_available
              ? `${count(chapters.practice_card_count, 'card')} from finished lessons`
              : 'Finish a lesson first'}
          </span>
        </button>
      </div>

      <h2 className="section-title">Lessons</h2>
      {chapters.chapters.map(chapter => (
        <ChapterCard key={chapter.id} chapter={chapter} nextLessonId={next?.id ?? null} onStart={onStart} />
      ))}
    </div>
  );
}
