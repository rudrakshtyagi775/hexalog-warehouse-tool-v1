// Official Hexalog brand mark — verified against https://webcdn.hexalog.in/logo.svg.
// Single shared copy so Sidebar, Landing, and Login all render the same asset.
export function HexalogLogo({
  width = 46,
  height = 51,
  className,
}: {
  width?: number
  height?: number
  className?: string
}) {
  return (
    <svg
      width={width}
      height={height}
      viewBox="0 0 46 51"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
    >
      <path d="M7.65511 8.68054L0.531124 12.8892L22.545 25.9025L30.0921 21.4836L23.059 17.3109L29.4774 13.5935L36.7217 17.8372L44.6218 13.0669L22.7488 0.0540079L14.6374 4.82372L20.6156 8.35993L13.9853 12.288L7.65511 8.68054Z" fill="#8F62DF"/>
      <path d="M22.2544 27L0.492188 14L0.0276275 37.9458L22.2544 50.3257L22.2544 27Z" fill="#442A59"/>
      <path d="M45.3879 14.1025L23.0992 27.2985L23.0992 50.4686L45.3866 37.9153L45.3879 14.1025Z" fill="#442A59"/>
      <path d="M22.9922 27L45.4922 14V38L22.9922 50.5V27Z" fill="#744C8A"/>
    </svg>
  )
}
