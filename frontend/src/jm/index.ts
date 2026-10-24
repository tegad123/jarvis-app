/**
 * Jarvis Mono components (React 18 + Tailwind 3).
 *
 * Needs, once on the page: ./fonts/fonts.css and ./tokens/tokens.css, and
 * the Tailwind preset in ./preset.cjs with this folder in `content`.
 */
export { Button, type ButtonProps } from './Button/Button'
export { Sidebar, type SidebarProps, type SidebarNavItem } from './Sidebar/Sidebar'
export { ConversationRow, type ConversationRowProps } from './ConversationRow/ConversationRow'
export { MessageBubble, type MessageBubbleProps } from './MessageBubble/MessageBubble'
export { SystemLine, type SystemLineProps } from './SystemLine/SystemLine'
export { StepList, type StepListProps, type Step } from './StepList/StepList'
export { ComputerCard, type ComputerCardProps, type ComputerAsk, type ComputerInput } from './ComputerCard/ComputerCard'
export { ApprovalCard, type ApprovalCardProps, type ApprovalChange } from './ApprovalCard/ApprovalCard'
export { Composer, type ComposerProps, type VoiceState } from './Composer/Composer'
export { SettingsSection, type SettingsSectionProps } from './SettingsSection/SettingsSection'
export { PairingCode, type PairingCodeProps } from './PairingCode/PairingCode'
export { SignIn, formatPin, type SignInProps, type Person } from './SignIn/SignIn'
export { TodaySummary, type TodaySummaryProps } from './TodaySummary/TodaySummary'
export { ActivityRow, type ActivityRowProps } from './ActivityRow/ActivityRow'
export { TaskRow, type TaskRowProps, type TaskStatus } from './TaskRow/TaskRow'
export { LeadRow, type LeadRowProps } from './LeadRow/LeadRow'
export { FilterChips, type FilterChipsProps } from './FilterChips/FilterChips'
export { NewChannelSheet, type NewChannelSheetProps } from './NewChannelSheet/NewChannelSheet'
export { MembersSheet, type MembersSheetProps } from './MembersSheet/MembersSheet'
export { EmptyState, type EmptyStateProps } from './EmptyState/EmptyState'
export { ErrorState, type ErrorStateProps } from './ErrorState/ErrorState'
export { OutboxRow, type OutboxRowProps } from './OutboxRow/OutboxRow'
export { Icon, type IconName } from './shared/Icon'
export { List, Rows, Field, type ChatAttachment } from './shared/primitives'
