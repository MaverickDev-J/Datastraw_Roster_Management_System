export type Row = Record<string, any>;
export interface Assignment { employee_id: string; date: string; duty_id: string; locked: boolean }
export interface Issue {code:string;reason:string;severity:string;date?:string;block?:string;employee_id?:string;brand_id?:string;process_id?:string}
export interface Coverage { date:string;brand_id:string;process_id:string;block:string;required:number;actual:number;employees:string[];safe_actual?:number;conflicted_employees?:string[];conflicts?:string[];status?:'CLOSED'|'GAP'|'CONFLICT'|'COVERED' }
export interface Report { valid:boolean;issues:Issue[];warnings:Issue[];coverage:Coverage[];required_blocks:number;covered_blocks:number;shortage:number;lookahead_issues?:Issue[];lookahead_coverage?:Coverage[] }
export interface Roster {id:string;month:string;revision:number;config_revision:number;status:string;stale:boolean;history_changed?:boolean;created_at:string;data:{config:Configuration;assignments:Assignment[];validation:Report;seconds:number;solver_status:string;strict_status:string;engine:string;parent_id?:string;changes:{added:Assignment[];removed:Assignment[]}}}
export interface Configuration {revision:number;employees:Row[];pods:Row[];brands:Row[];processes:Row[];brand_processes:Row[];duties:Row[];allocations:Row[];absences:Row[];holidays:Row[];overrides:Row[];[key:string]:Row[]|number}
