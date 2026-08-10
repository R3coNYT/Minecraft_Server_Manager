/**
 * Tête d'un joueur, découpée dans son skin.
 *
 * Un skin Minecraft est une image de 64×64 pixels dont la face avant de la tête
 * occupe le carré (8,8)→(16,16). Plutôt que de générer une vignette côté serveur
 * — ce qui imposerait une bibliothèque de traitement d'image — on recadre en CSS
 * pur : agrandissement ×8, décalage de 8 pixels, et rendu **pixelisé** pour
 * conserver l'aspect voulu au lieu d'un flou d'interpolation.
 *
 * L'image est servie par MSM, jamais chargée depuis un tiers.
 */

import { useState } from 'react'
import { api } from '@/lib/api'
import { cn } from '@/lib/cn'

interface PlayerAvatarProps {
  serverId: number
  username: string
  hasUuid: boolean
  size?: number
  className?: string
}

export function PlayerAvatar({
  serverId,
  username,
  hasUuid,
  size = 32,
  className,
}: PlayerAvatarProps) {
  const [failed, setFailed] = useState(false)

  // Sans UUID (serveur en mode hors ligne) ou après un échec, on affiche les
  // initiales : inventer un avatar serait pire que d'assumer l'absence.
  if (!hasUuid || failed) {
    return (
      <span
        className={cn(
          'flex shrink-0 items-center justify-center rounded bg-slate-800 font-semibold text-slate-300',
          className,
        )}
        style={{ width: size, height: size, fontSize: size * 0.36 }}
        aria-hidden
      >
        {username.slice(0, 2).toUpperCase()}
      </span>
    )
  }

  return (
    <span
      className={cn('relative block shrink-0 overflow-hidden rounded bg-slate-800', className)}
      style={{ width: size, height: size }}
    >
      <img
        src={api.players.skinUrl(serverId, username)}
        alt=""
        onError={() => setFailed(true)}
        className="absolute max-w-none"
        style={{
          width: size * 8, // le skin fait 64 px de large, la tête 8 px
          height: size * 8,
          left: -size,
          top: -size,
          imageRendering: 'pixelated',
        }}
      />
    </span>
  )
}
