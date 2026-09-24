/** Avatar d'un compte : son image si elle existe, sinon l'initiale de son pseudo. */

import { cn } from '@/lib/cn'

const SIZES = {
  xs: 'size-5 text-[10px]',
  sm: 'size-7 text-xs',
  md: 'size-9 text-sm',
  lg: 'size-20 text-2xl',
} as const

export function Avatar({
  url,
  name,
  size = 'sm',
  className,
}: {
  url: string | null | undefined
  name: string
  size?: keyof typeof SIZES
  className?: string
}) {
  const classes = cn('shrink-0 rounded-full ring-1 ring-slate-700', SIZES[size], className)
  if (url) return <img src={url} alt="" className={cn(classes, 'object-cover')} />
  return (
    <span
      aria-hidden
      className={cn(
        classes,
        'flex items-center justify-center bg-emerald-900/60 font-semibold uppercase text-emerald-200',
      )}
    >
      {name.slice(0, 1) || '?'}
    </span>
  )
}
