// Démineur - Jeu

class Demineur {
    constructor() {
        this.board = [];
        this.mines = [];
        this.flags = new Set();
        this.revealed = new Set();
        this.gameOver = false;
        this.gameWon = false;
        this.startTime = null;
        this.timerInterval = null;
        this.rows = 9;
        this.cols = 9;
        this.mineCount = 10;
        this.totalCells = this.rows * this.cols;
        this.difficulty = 'easy';

        this.init();
    }

    init() {
        this.setDifficulty('easy');
        this.createBoard();
        this.renderBoard();
        this.updateStats();
        this.startTimer();
    }

    setDifficulty(level) {
        this.difficulty = level;
        switch(level) {
            case 'easy':
                this.rows = 9;
                this.cols = 9;
                this.mineCount = 10;
                break;
            case 'medium':
                this.rows = 16;
                this.cols = 16;
                this.mineCount = 40;
                break;
            case 'hard':
                this.rows = 16;
                this.cols = 30;
                this.mineCount = 99;
                break;
        }
        this.totalCells = this.rows * this.cols;
        this.reset();
    }

    createBoard() {
        this.board = [];
        for (let r = 0; r < this.rows; r++) {
            this.board[r] = [];
            for (let c = 0; c < this.cols; c++) {
                this.board[r][c] = 0; // 0 = pas de mine, les nombres seront calculés plus tard
            }
        }
        this.placeMines();
        this.calculateNumbers();
    }

    placeMines() {
        this.mines = [];
        let placed = 0;
        while (placed < this.mineCount) {
            const r = Math.floor(Math.random() * this.rows);
            const c = Math.floor(Math.random() * this.cols);
            if (this.board[r][c] !== 'M') {
                this.board[r][c] = 'M';
                this.mines.push([r, c]);
                placed++;
            }
        }
    }

    calculateNumbers() {
        // Pour chaque case, si c'est une mine, on ignore
        // Sinon, on compte les mines autour
        for (let r = 0; r < this.rows; r++) {
            for (let c = 0; c < this.cols; c++) {
                if (this.board[r][c] === 'M') continue;
                let count = 0;
                for (let dr = -1; dr <= 1; dr++) {
                    for (let dc = -1; dc <= 1; dc++) {
                        if (dr === 0 && dc === 0) continue;
                        const nr = r + dr;
                        const nc = c + dc;
                        if (nr >= 0 && nr < this.rows && nc >= 0 && nc < this.cols) {
                            if (this.board[nr][nc] === 'M') count++;
                        }
                    }
                }
                this.board[r][c] = count;
            }
        }
    }

    renderBoard() {
        const boardEl = document.getElementById('board');
        boardEl.innerHTML = '';
        boardEl.style.gridTemplateColumns = `repeat(${this.cols}, 1fr)`;
        for (let r = 0; r < this.rows; r++) {
            for (let c = 0; c < this.cols; c++) {
                const cell = document.createElement('div');
                cell.className = 'cell';
                cell.dataset.row = r;
                cell.dataset.col = c;
                cell.addEventListener('click', (e) => this.handleLeftClick(r, c));
                cell.addEventListener('contextmenu', (e) => {
                    e.preventDefault();
                    this.handleRightClick(r, c);
                });
                boardEl.appendChild(cell);
            }
        }
    }

    updateCell(r, c) {
        const cell = document.querySelector(`.cell[data-row="${r}"][data-col="${c}"]`);
        if (!cell) return;
        const key = `${r},${c}`;
        if (this.flags.has(key)) {
            cell.classList.add('flag');
            cell.textContent = '';
        } else if (this.revealed.has(key)) {
            cell.classList.remove('flag');
            cell.classList.add('revealed');
            const val = this.board[r][c];
            if (val === 'M') {
                cell.classList.add('mine');
                cell.textContent = '';
            } else if (val > 0) {
                cell.textContent = val;
                cell.classList.add(`num-${val}`);
            } else {
                cell.textContent = '';
            }
        } else {
            cell.className = 'cell';
            cell.textContent = '';
        }
    }

    handleLeftClick(r, c) {
        if (this.gameOver || this.gameWon) return;
        const key = `${r},${c}`;
        if (this.flags.has(key) || this.revealed.has(key)) return;
        this.reveal(r, c);
    }

    handleRightClick(r, c) {
        if (this.gameOver || this.gameWon) return;
        const key = `${r},${c}`;
        if (this.revealed.has(key)) return;
        if (this.flags.has(key)) {
            this.flags.delete(key);
        } else {
            this.flags.add(key);
        }
        this.updateCell(r, c);
        this.updateStats();
        this.checkWin();
    }

    reveal(r, c) {
        const key = `${r},${c}`;
        if (this.revealed.has(key)) return;
        this.revealed.add(key);
        this.updateCell(r, c);
        if (this.board[r][c] === 'M') {
            this.gameOver = true;
            this.revealAllMines();
            this.showStatus('Perdu ! Vous avez déclenché une mine.', 'lose');
            return;
        }
        // Si la case est vide (0), on révèle récursivement les cases autour
        if (this.board[r][c] === 0) {
            for (let dr = -1; dr <= 1; dr++) {
                for (let dc = -1; dc <= 1; dc++) {
                    if (dr === 0 && dc === 0) continue;
                    const nr = r + dr;
                    const nc = c + dc;
                    if (nr >= 0 && nr < this.rows && nc >= 0 && nc < this.cols) {
                        const nKey = `${nr},${nc}`;
                        if (!this.revealed.has(nKey) && !this.flags.has(nKey)) {
                            this.reveal(nr, nc);
                        }
                    }
                }
            }
        }
        this.updateStats();
        this.checkWin();
    }

    revealAllMines() {
        for (const [r, c] of this.mines) {
            const key = `${r},${c}`;
            if (!this.flags.has(key)) {
                this.revealed.add(key);
                this.updateCell(r, c);
            }
        }
    }

    checkWin() {
        // Toutes les cases non-mines sont révélées
        const nonMineCells = this.totalCells - this.mineCount;
        if (this.revealed.size === nonMineCells) {
            this.gameWon = true;
            this.showStatus('Félicitations ! Vous avez gagné !', 'win');
        }
    }

    updateStats() {
        document.getElementById('mines-count').textContent = this.mineCount;
        const cellsLeft = this.totalCells - this.revealed.size - this.mineCount;
        document.getElementById('cells-left').textContent = cellsLeft > 0 ? cellsLeft : 0;
    }

    startTimer() {
        if (this.timerInterval) clearInterval(this.timerInterval);
        this.startTime = Date.now();
        this.timerInterval = setInterval(() => {
            if (this.gameOver || this.gameWon) return;
            const elapsed = Math.floor((Date.now() - this.startTime) / 1000);
            document.getElementById('timer').textContent = elapsed;
        }, 1000);
    }

    showStatus(msg, className) {
        const statusEl = document.getElementById('game-status');
        statusEl.textContent = msg;
        statusEl.className = 'status ' + (className || '');
    }

    reset() {
        this.flags.clear();
        this.revealed.clear();
        this.gameOver = false;
        this.gameWon = false;
        this.createBoard();
        this.renderBoard();
        this.updateStats();
        this.startTimer();
        this.showStatus('Prêt à jouer !', '');
    }
}

// Initialisation du jeu
let game;

document.addEventListener('DOMContentLoaded', () => {
    game = new Demineur();

    // Gestionnaires d'événements
    document.getElementById('restart-btn').addEventListener('click', () => {
        game.reset();
    });

    document.getElementById('difficulty').addEventListener('change', (e) => {
        game.setDifficulty(e.target.value);
    });

    document.getElementById('help-btn').addEventListener('click', () => {
        alert('Instructions :\n- Clic gauche : révéler une case\n- Clic droit : poser/enlever un drapeau\n- Objectif : révéler toutes les cases sans mines.\n\nLes nombres indiquent combien de mines sont adjacentes.');
    });
});
