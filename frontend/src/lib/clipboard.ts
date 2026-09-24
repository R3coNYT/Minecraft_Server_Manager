/** Presse-papiers, y compris hors HTTPS. */

/**
 * Copie dans le presse-papiers. `navigator.clipboard` n'existe qu'en contexte
 * sécurisé (HTTPS ou localhost) : un panneau consulté en HTTP sur le réseau
 * local passe par l'ancienne méthode, via un champ temporaire.
 */
export async function copyText(text: string): Promise<boolean> {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      // Refus du navigateur : tenter l'ancienne méthode.
    }
  }
  const field = document.createElement('textarea')
  field.value = text
  field.setAttribute('readonly', '')
  field.style.position = 'fixed'
  field.style.opacity = '0'
  document.body.appendChild(field)
  field.select()
  try {
    return document.execCommand('copy')
  } catch {
    return false
  } finally {
    field.remove()
  }
}
