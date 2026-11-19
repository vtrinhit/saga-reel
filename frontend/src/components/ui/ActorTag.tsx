import { Bot, User } from 'lucide-react';
import { cn } from '../../lib/cn';

export interface ActorTagProps {
  /** Actor của change set / việc: `agent:<tên>`, `llm:<…>`, `user:<id>`, `owner`, `viewer`, `system`… */
  actor: string;
  /** Tên hiển thị cho người dùng hiện tại (mặc định "Bạn"). */
  selfLabel?: string;
  className?: string;
}

/** Tên ngắn của actor: agent/llm bỏ tiền tố; người dùng → "Bạn"; hệ thống → "Hệ thống". */
export function actorLabel(actor: string, selfLabel = 'Bạn'): { kind: 'agent' | 'user' | 'system'; name: string } {
  if (actor.startsWith('agent:')) return { kind: 'agent', name: actor.slice(6) || 'agent' };
  if (actor.startsWith('llm:')) return { kind: 'agent', name: `llm-${actor.slice(4)}`.replace(/-$/, '') };
  if (actor === 'system' || actor.startsWith('system:') || actor.startsWith('job:')) return { kind: 'system', name: 'Hệ thống' };
  return { kind: 'user', name: selfLabel };
}

/** Icon `Bot`/`User` 12 + tên; agent dùng mono, người dùng dùng font UI (DESIGN §5.6). Agent không có màu riêng. */
export function ActorTag({ actor, selfLabel, className }: ActorTagProps) {
  const { kind, name } = actorLabel(actor, selfLabel);
  const Icon = kind === 'user' ? User : Bot;
  return (
    <span
      className={cn('inline-flex min-w-0 items-center gap-1 whitespace-nowrap text-fg-2', className)}
      title={actor}
    >
      <Icon className="h-3 w-3 flex-none" strokeWidth={1.75} aria-hidden />
      <span className={cn('truncate', kind === 'agent' ? 'font-mono text-xs' : 'text-xs')}>{name}</span>
    </span>
  );
}
