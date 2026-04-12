export function SakuraIcon({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden>
      <path
        d="M12 2C12 2 9.5 6.5 9.5 10C9.5 12.5 10.5 14 12 15C13.5 14 14.5 12.5 14.5 10C14.5 6.5 12 2 12 2Z"
        fill="currentColor"
        opacity="0.85"
      />
      <path
        d="M12 15C10.5 16 8 16.5 5.5 15.5C3 14.5 2 12 2 12C2 12 4 14.5 7 15C9 15.3 11 14.8 12 15Z"
        fill="currentColor"
        opacity="0.6"
      />
      <path
        d="M12 15C13.5 16 16 16.5 18.5 15.5C21 14.5 22 12 22 12C22 12 20 14.5 17 15C15 15.3 13 14.8 12 15Z"
        fill="currentColor"
        opacity="0.6"
      />
      <circle cx="12" cy="15" r="1.5" fill="currentColor" opacity="0.9" />
    </svg>
  );
}
