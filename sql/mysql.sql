-- MySQL 8.0+ 初始化脚本（由 mingjian/infra/schema.py 生成，请勿手工修改）
-- 前置：先创建 utf8mb4 数据库，本脚本只建表与索引。
-- 用法：mysql -u USER -p DB < sql/mysql.sql
-- 字符集固定 utf8mb4，避免中文与 emoji 被截断。
SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS `entities` (
  `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `key` VARCHAR(255) UNIQUE,
  `name` VARCHAR(255),
  `short_name` VARCHAR(255),
  `full_name` VARCHAR(255),
  `code` VARCHAR(255),
  `org_id` VARCHAR(255),
  `market` VARCHAR(255),
  `resolved_by` VARCHAR(255),
  `created_at` VARCHAR(255),
  `updated_at` VARCHAR(255)
);

CREATE TABLE IF NOT EXISTS `runs` (
  `id` VARCHAR(255) PRIMARY KEY,
  `entity_key` VARCHAR(255),
  `company` VARCHAR(255),
  `status` VARCHAR(255),
  `started_at` VARCHAR(255),
  `finished_at` VARCHAR(255),
  `params` TEXT,
  `stats` TEXT,
  `judgments` TEXT,
  `error` TEXT
);

CREATE TABLE IF NOT EXISTS `documents` (
  `id` VARCHAR(255) PRIMARY KEY,
  `run_id` VARCHAR(255),
  `entity_key` VARCHAR(255),
  `kind` VARCHAR(255),
  `title` TEXT,
  `url` TEXT,
  `source` VARCHAR(255),
  `published_at` VARCHAR(255),
  `body` LONGTEXT,
  `evidence_level` VARCHAR(255),
  `fetched_at` VARCHAR(255),
  `extra` TEXT,
  KEY `idx_doc_run` (`run_id`),
  KEY `idx_doc_entity` (`entity_key`)
);

CREATE TABLE IF NOT EXISTS `facts` (
  `id` VARCHAR(255) PRIMARY KEY,
  `run_id` VARCHAR(255),
  `entity_key` VARCHAR(255),
  `doc_id` VARCHAR(255),
  `type` VARCHAR(255),
  `type_label` VARCHAR(255),
  `statement` TEXT,
  `evidence_quote` TEXT,
  `amount` VARCHAR(255),
  `event_date` VARCHAR(255),
  `confidence` DOUBLE,
  `dedupe_key` VARCHAR(255),
  `corroboration` INT DEFAULT 1,
  `conflict` INT DEFAULT 0,
  `conflict_note` TEXT,
  `created_at` VARCHAR(255),
  KEY `idx_fact_run` (`run_id`),
  KEY `idx_fact_entity` (`entity_key`)
);

CREATE TABLE IF NOT EXISTS `snapshots` (
  `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `entity_key` VARCHAR(255),
  `run_id` VARCHAR(255),
  `run_at` VARCHAR(255),
  `facts` LONGTEXT,
  KEY `idx_snap_entity` (`entity_key`)
);

CREATE TABLE IF NOT EXISTS `subjects` (
  `key` VARCHAR(255) PRIMARY KEY,
  `name` VARCHAR(255),
  `data` LONGTEXT,
  `created_at` VARCHAR(255),
  `updated_at` VARCHAR(255)
);

CREATE TABLE IF NOT EXISTS `audit` (
  `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `entity_key` VARCHAR(255),
  `run_id` VARCHAR(255),
  `ts` VARCHAR(255),
  `actor` VARCHAR(255),
  `action` VARCHAR(255),
  `target` VARCHAR(255),
  `old_value` TEXT,
  `new_value` TEXT,
  `reason` TEXT
);

CREATE TABLE IF NOT EXISTS `run_events` (
  `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `run_id` VARCHAR(255) NOT NULL,
  `seq` INT NOT NULL,
  `type` VARCHAR(255),
  `payload` LONGTEXT,
  `created_at` VARCHAR(255),
  UNIQUE KEY `idx_event_run_seq` (`run_id`, `seq`)
);

CREATE TABLE IF NOT EXISTS `checkpoints` (
  `run_id` VARCHAR(255) NOT NULL,
  `step` INT NOT NULL,
  `payload` LONGTEXT,
  `created_at` VARCHAR(255),
  PRIMARY KEY (`run_id`, `step`)
);

CREATE TABLE IF NOT EXISTS `schedules` (
  `id` VARCHAR(255) PRIMARY KEY,
  `name` VARCHAR(255),
  `kind` VARCHAR(255),
  `params` TEXT,
  `spec` TEXT,
  `enabled` INT DEFAULT 1,
  `last_run_at` VARCHAR(255),
  `last_run_id` VARCHAR(255),
  `next_run_at` VARCHAR(255),
  `created_at` VARCHAR(255),
  `updated_at` VARCHAR(255),
  KEY `idx_sched_next` (`enabled`, `next_run_at`)
);

CREATE TABLE IF NOT EXISTS `memory` (
  `id` VARCHAR(255) PRIMARY KEY,
  `scope` VARCHAR(255) NOT NULL DEFAULT 'global',
  `key` VARCHAR(255) NOT NULL DEFAULT '',
  `kind` VARCHAR(255) NOT NULL DEFAULT 'note',
  `content` LONGTEXT NOT NULL,
  `refs` VARCHAR(2048) NOT NULL DEFAULT '[]',
  `source` VARCHAR(255) NOT NULL DEFAULT '',
  `confidence` DOUBLE,
  `hits` INT NOT NULL DEFAULT 0,
  `created_at` VARCHAR(255) NOT NULL,
  `updated_at` VARCHAR(255) NOT NULL,
  `used_at` VARCHAR(255),
  KEY `idx_memory_scope` (`scope`),
  KEY `idx_memory_kind` (`kind`)
);

CREATE TABLE IF NOT EXISTS `companies` (
  `key` VARCHAR(255) PRIMARY KEY,
  `name` VARCHAR(255),
  `credit_code` VARCHAR(64),
  `legal_person` VARCHAR(255),
  `reg_capital` VARCHAR(255),
  `reg_status` VARCHAR(255),
  `establish_date` VARCHAR(255),
  `location` TEXT,
  `website` VARCHAR(255),
  `industry` VARCHAR(255),
  `business` LONGTEXT,
  `source` VARCHAR(255),
  `raw` LONGTEXT,
  `fetched_at` VARCHAR(255),
  KEY `idx_company_name` (`name`),
  KEY `idx_company_code` (`credit_code`)
);

CREATE TABLE IF NOT EXISTS `settings` (
  `key` VARCHAR(255) PRIMARY KEY,
  `value` LONGTEXT NOT NULL,
  `updated_at` VARCHAR(255) NOT NULL
);
