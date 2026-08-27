/**
 * The script that picks a theme before the first paint.
 *
 * A string, because it runs inline in the document head: anything that waits
 * for hydration flashes the wrong theme first. Kept in its own module rather
 * than inline in layout.tsx so it can be tested, which layout.tsx cannot be
 * (it imports CSS and the Clerk provider).
 *
 * A stored choice wins; with no stored choice, follow the device.
 *
 * It used to default to light and consult nothing else, so a phone set to dark
 * loaded the site light and the browser force-darkened it. That is not a
 * neutral transform: it repaints plain colours but cannot touch a
 * `background-clip: text` gradient, so the landing headline kept its
 * light-theme ink and rendered near-black on the darkened background, while
 * everything around it inverted and looked fine. The `color-scheme`
 * declarations in globals.css are the other half, and the part that tells a
 * browser not to do it at all.
 */
export const themeInit =
  `try{var s=localStorage.getItem('theme');` +
  `if(s==='dark'||(!s&&matchMedia('(prefers-color-scheme: dark)').matches)){` +
  `document.documentElement.classList.add('dark')}}catch(e){}`;
