-- ═══════════════════════════════════════════════════════════
-- {{PROJECT_TITLE}} — Schéma de base de données
-- ═══════════════════════════════════════════════════════════

CREATE DATABASE IF NOT EXISTS `{{PROJECT_NAME}}` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE `{{PROJECT_NAME}}`;

-- Table Utilisateurs
CREATE TABLE IF NOT EXISTS `users` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `name` VARCHAR(100) NOT NULL,
  `email` VARCHAR(255) NOT NULL UNIQUE,
  `password_hash` VARCHAR(255) NOT NULL,
  `role` ENUM('admin', 'editor', 'user') DEFAULT 'user',
  `status` ENUM('active', 'pending', 'suspended') DEFAULT 'pending',
  `avatar_url` VARCHAR(500) DEFAULT NULL,
  `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  `updated_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX `idx_email` (`email`),
  INDEX `idx_role` (`role`),
  INDEX `idx_status` (`status`)
) ENGINE=InnoDB;

-- Table Sessions
CREATE TABLE IF NOT EXISTS `sessions` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `user_id` INT NOT NULL,
  `token` VARCHAR(500) NOT NULL UNIQUE,
  `expires_at` TIMESTAMP NOT NULL,
  `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB;

-- Table Logs d'activité
CREATE TABLE IF NOT EXISTS `activity_log` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `user_id` INT DEFAULT NULL,
  `action` VARCHAR(100) NOT NULL,
  `details` TEXT DEFAULT NULL,
  `ip_address` VARCHAR(45) DEFAULT NULL,
  `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE SET NULL,
  INDEX `idx_action` (`action`),
  INDEX `idx_created` (`created_at`)
) ENGINE=InnoDB;

-- ═══ DONNÉES DE DÉMO ═══

INSERT INTO `users` (`name`, `email`, `password_hash`, `role`, `status`) VALUES
('Marie Dupont', 'marie@example.com', '$2y$10$demoHashAdminMarieDupont000000000000000000000000', 'admin', 'active'),
('Jean Martin', 'jean@example.com', '$2y$10$demoHashEditorJeanMartin000000000000000000000000', 'editor', 'active'),
('Sophie Bernard', 'sophie@example.com', '$2y$10$demoHashUserSophieBernard0000000000000000000000', 'user', 'pending'),
('Pierre Leroy', 'pierre@example.com', '$2y$10$demoHashUserPierreLeroy00000000000000000000000000', 'user', 'active'),
('Claire Moreau', 'claire@example.com', '$2y$10$demoHashEditorClairMoreau0000000000000000000000', 'editor', 'suspended'),
('Thomas Petit', 'thomas@example.com', '$2y$10$demoHashUserThomasPetit00000000000000000000000000', 'user', 'active');

INSERT INTO `activity_log` (`user_id`, `action`, `details`, `ip_address`) VALUES
(1, 'login', 'Connexion admin réussie', '192.168.1.10'),
(2, 'edit', 'Article "Bienvenue" modifié', '192.168.1.11'),
(1, 'create_user', 'Utilisateur Sophie Bernard créé', '192.168.1.10'),
(4, 'login', 'Première connexion', '10.0.0.5'),
(1, 'settings', 'Configuration SMTP mise à jour', '192.168.1.10');
