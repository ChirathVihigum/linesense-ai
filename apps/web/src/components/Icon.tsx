/**
 * The app's icon set: Phosphor (regular weight), imported per icon so tests and
 * the bundle only load what is used. Icons are decorative and always sit next to
 * text, so they are hidden from assistive technology.
 */
import type { Icon as PhosphorIcon } from '@phosphor-icons/react'
import { ArrowsClockwiseIcon } from '@phosphor-icons/react/dist/csr/ArrowsClockwise'
import { BellIcon } from '@phosphor-icons/react/dist/csr/Bell'
import { CalculatorIcon } from '@phosphor-icons/react/dist/csr/Calculator'
import { CalendarBlankIcon } from '@phosphor-icons/react/dist/csr/CalendarBlank'
import { CaretDownIcon } from '@phosphor-icons/react/dist/csr/CaretDown'
import { ChatTextIcon } from '@phosphor-icons/react/dist/csr/ChatText'
import { CheckIcon } from '@phosphor-icons/react/dist/csr/Check'
import { CheckCircleIcon } from '@phosphor-icons/react/dist/csr/CheckCircle'
import { ChecksIcon } from '@phosphor-icons/react/dist/csr/Checks'
import { CircleDashedIcon } from '@phosphor-icons/react/dist/csr/CircleDashed'
import { ClockIcon } from '@phosphor-icons/react/dist/csr/Clock'
import { DownloadSimpleIcon } from '@phosphor-icons/react/dist/csr/DownloadSimple'
import { EyeIcon } from '@phosphor-icons/react/dist/csr/Eye'
import { FactoryIcon } from '@phosphor-icons/react/dist/csr/Factory'
import { FileCsvIcon } from '@phosphor-icons/react/dist/csr/FileCsv'
import { FileTextIcon } from '@phosphor-icons/react/dist/csr/FileText'
import { FlagCheckeredIcon } from '@phosphor-icons/react/dist/csr/FlagCheckered'
import { FlaskIcon } from '@phosphor-icons/react/dist/csr/Flask'
import { GearIcon } from '@phosphor-icons/react/dist/csr/Gear'
import { HourglassIcon } from '@phosphor-icons/react/dist/csr/Hourglass'
import { InfoIcon } from '@phosphor-icons/react/dist/csr/Info'
import { ListIcon } from '@phosphor-icons/react/dist/csr/List'
import { LockIcon } from '@phosphor-icons/react/dist/csr/Lock'
import { LockOpenIcon } from '@phosphor-icons/react/dist/csr/LockOpen'
import { MagnifyingGlassIcon } from '@phosphor-icons/react/dist/csr/MagnifyingGlass'
import { PaperPlaneTiltIcon } from '@phosphor-icons/react/dist/csr/PaperPlaneTilt'
import { PencilSimpleIcon } from '@phosphor-icons/react/dist/csr/PencilSimple'
import { PlayCircleIcon } from '@phosphor-icons/react/dist/csr/PlayCircle'
import { PlusIcon } from '@phosphor-icons/react/dist/csr/Plus'
import { ProhibitIcon } from '@phosphor-icons/react/dist/csr/Prohibit'
import { QuestionIcon } from '@phosphor-icons/react/dist/csr/Question'
import { ShieldCheckIcon } from '@phosphor-icons/react/dist/csr/ShieldCheck'
import { SignOutIcon } from '@phosphor-icons/react/dist/csr/SignOut'
import { SparkleIcon } from '@phosphor-icons/react/dist/csr/Sparkle'
import { StackIcon } from '@phosphor-icons/react/dist/csr/Stack'
import { TruckIcon } from '@phosphor-icons/react/dist/csr/Truck'
import { UploadSimpleIcon } from '@phosphor-icons/react/dist/csr/UploadSimple'
import { UserCheckIcon } from '@phosphor-icons/react/dist/csr/UserCheck'
import { UsersIcon } from '@phosphor-icons/react/dist/csr/Users'
import { WarningIcon } from '@phosphor-icons/react/dist/csr/Warning'
import { XIcon } from '@phosphor-icons/react/dist/csr/X'
import { XCircleIcon } from '@phosphor-icons/react/dist/csr/XCircle'

const ICONS = {
  pencil: PencilSimpleIcon,
  'check-circle': CheckCircleIcon,
  check: CheckIcon,
  'check-double': ChecksIcon,
  calendar: CalendarBlankIcon,
  play: PlayCircleIcon,
  flag: FlagCheckeredIcon,
  truck: TruckIcon,
  'x-circle': XCircleIcon,
  x: XIcon,
  question: QuestionIcon,
  alert: WarningIcon,
  ban: ProhibitIcon,
  'circle-dashed': CircleDashedIcon,
  clock: ClockIcon,
  lock: LockIcon,
  unlock: LockOpenIcon,
  refresh: ArrowsClockwiseIcon,
  eye: EyeIcon,
  send: PaperPlaneTiltIcon,
  hourglass: HourglassIcon,
  layers: StackIcon,
  calculator: CalculatorIcon,
  sparkle: SparkleIcon,
  beaker: FlaskIcon,
  'user-check': UserCheckIcon,
  info: InfoIcon,
  menu: ListIcon,
  'caret-down': CaretDownIcon,
  factory: FactoryIcon,
  'sign-out': SignOutIcon,
  download: DownloadSimpleIcon,
  upload: UploadSimpleIcon,
  search: MagnifyingGlassIcon,
  plus: PlusIcon,
  'file-csv': FileCsvIcon,
  bell: BellIcon,
  'chat-text': ChatTextIcon,
  'file-text': FileTextIcon,
  gear: GearIcon,
  'shield-check': ShieldCheckIcon,
  users: UsersIcon,
} satisfies Record<string, PhosphorIcon>

export type IconName = keyof typeof ICONS

export function Icon({ name, className }: { name: IconName; className?: string }) {
  const Glyph = ICONS[name]
  return (
    <Glyph
      aria-hidden="true"
      focusable="false"
      data-icon={name}
      weight="regular"
      className={className ?? 'h-4 w-4 shrink-0'}
    />
  )
}
