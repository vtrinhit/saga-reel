import { cn } from '../../lib/cn';
import { evidenceSource, type BibleEvidence } from '../../lib/story';

/** Bằng chứng trích dẫn: vạch trái line-2, chữ trích nguyên văn trong ngoặc kép + nguồn. */
export function EvidenceQuote({
  evidence,
  seqNumber,
  className,
}: {
  evidence: Pick<BibleEvidence, 'quote' | 'source' | 'sequence_id'>;
  seqNumber?: (id: number) => number | null;
  className?: string;
}) {
  return (
    <blockquote className={cn('border-l-2 border-line-2 pl-2.5 text-xs text-fg-2', className)}>
      <span className="text-fg-1">“{evidence.quote}”</span> <cite className="not-italic text-fg-3">— {evidenceSource(evidence, seqNumber)}</cite>
    </blockquote>
  );
}
